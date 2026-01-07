#!/usr/bin/env python3
"""
        Fetch mails from IMAP-Server and forward them to Telegram Chat.

---

        Install dependencies:
            pip install -r requirements.txt
        Or via system packages (Debian/Arch):
            sudo apt install python3-python-telegram-bot python3-imaplib2 python3-bs4
            yay -Su python-telegram-bot python-imaplib2 python-beautifulsoup4

"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import typing
import sys
import re
import unicodedata
import argparse
import html
import socket
import time
import configparser
import email
from email.utils import parsedate_to_datetime
from datetime import timedelta, timezone, datetime
from enum import Enum

try:
    from bs4 import BeautifulSoup, Comment
    import imaplib2
except ImportError as import_error:
    logging.critical(import_error.__class__.__name__ + ": " + import_error.args[0])
    sys.exit(2)

"""
    Mail2TelegramForwarder:
                    A python script that fetches mails from remote IMAP mail server
                    and forward body and/or attachments to Telegram chat/user.

"""

__appname__ = "Mail to Telegram Forwarder"
__version__ = "0.4.0"


from telegram import error, Message, PhotoSize, Bot, ChatFullInfo
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown


class Tool:
    mask_error_data: list[str]

    def __init__(self):
        self.mask_error_data = []

    def decode_mail_data(self, value) -> str:
        result = ''
        for msg_part in email.header.decode_header(value):
            part, encoding = msg_part
            result += self.binary_to_string(part, encoding=encoding)
        return result

    @staticmethod
    def binary_to_string(value, **kwargs) -> str:
        encoding = kwargs.get('encoding')
        if not encoding:
            encoding = 'utf-8'
        if type(value) is bytes:
            try:
                return str(bytes.decode(value, encoding=encoding, errors='replace'))
            except UnicodeDecodeError as decode_error:
                logging.error("Can not decode value: '%s' reason: %s" % (value, decode_error.reason))
                return ' ###decoder-error:%s### ' % decode_error.reason
        else:
            return str(value)

    def _convert_error_message(self, message) -> str:
        error_message: str = message
        if type(message) is bytes:
            error_message = self.binary_to_string(message)
        if type(message) is not str:
            error_message = '%s' % message  # ', '.join(map(str, message.args))
        try:
            _, _, tb = sys.exc_info()
            if tb is not None:
                frame = tb.tb_frame
                line_no = tb.tb_lineno
                obj_name = frame.f_code.co_name
                file_name = frame.f_code.co_filename
                trace_msg = " [%s:%s in '%s']" % (file_name, line_no, obj_name)
                error_message = '%s%s' % (error_message, trace_msg)
        except Exception as ex:
            logging.error('Fatal in "build_error_message": %s' % str(ex))
            logging.error('--- initial error: "%s"' % message)
        return error_message

    def build_error_message(self, message) -> str:
        error_message: str
        if type(message) is list:
            lines: list[str] = []
            for item in message:
                lines.append(self._convert_error_message(item))
            error_message = "; ".join(lines)
        else:
            error_message = self._convert_error_message(message)
        for mask in self.mask_error_data:
            error_message = error_message.replace(mask, '****')
        return error_message


class Config:
    config_parser = None
    tool: Tool

    imap_user = None
    imap_password = None
    imap_server = None
    imap_port = 993
    imap_timeout = 60
    imap_refresh = 10
    imap_push_mode = False
    imap_disconnect = False
    imap_folder = 'INBOX'
    imap_search = '(UID ${lastUID}:* UNSEEN)'
    imap_mark_as_read = False
    imap_max_length = 2000
    imap_read_old_mails = False
    imap_read_old_mails_processed = False
    imap_ignore_inline_image = ''
    imap_user = None
    imap_password = None
    imap_server = None
    imap_port = 993
    imap_timeout = 60
    imap_refresh = 5
    imap_push_mode = False
    imap_disconnect = False

    tg_bot_token = None
    tg_forward_to_chat_id = None
    tg_message_thread_id = None
    tg_prefer_html = True
    tg_markdown_version = 2
    tg_forward_mail_content = True
    tg_forward_attachment = True
    tg_forward_embedded_images = True
    tg_connection_read_timeout = 60
    tg_connection_write_timeout = 60
    tg_connection_connect_timeout = 60
    tg_connection_pool_timeout = 60
    tg_connection_pool_size = 256

    # Filtering settings
    filter_mode = 'disabled'  # disabled, whitelist, blacklist, combined
    filter_whitelist_keywords: list = []
    filter_blacklist_keywords: list = []
    filter_whitelist_authors: list = []
    filter_blacklist_authors: list = []

    def __init__(self, tool, cmd_args):
        """
            Parse config file for login credentials, address of remote mail server,
            telegram config and configuration of this application.
        """
        try:
            self.tool = tool
            self.config_parser = configparser.ConfigParser(interpolation=None)
            files = self.config_parser.read(cmd_args.config)
            if len(files) == 0:
                logging.critical("Error parsing config file: File '%s' not found!" % cmd_args.config)
                sys.exit(2)

            self.imap_user = self.get_config('Mail', 'user', self.imap_user)
            self.imap_password = self.get_config('Mail', 'password', self.imap_password)
            if self.imap_password:
                tool.mask_error_data.append(self.imap_password)
            self.imap_server = self.get_config('Mail', 'server', self.imap_server)
            self.imap_port = self.get_config('Mail', 'port', self.imap_port, int)
            self.imap_timeout = self.get_config('Mail', 'timeout', self.imap_timeout, int)
            self.imap_refresh = self.get_config('Mail', 'refresh', self.imap_refresh, int)
            self.imap_push_mode = self.get_config('Mail', 'push_mode', self.imap_push_mode, bool)
            self.imap_disconnect = self.get_config('Mail', 'disconnect', self.imap_disconnect, bool)
            self.imap_folder = self.get_config('Mail', 'folder', self.imap_folder)
            self.imap_read_old_mails = self.get_config('Mail', 'read_old_mails', self.imap_read_old_mails)
            self.imap_search = self.get_config('Mail', 'search', self.imap_search)
            self.imap_mark_as_read = self.get_config('Mail', 'mark_as_read', self.imap_mark_as_read, bool)
            self.imap_max_length = self.get_config('Mail', 'max_length', self.imap_max_length, int)
            self.imap_ignore_inline_image = self.get_config('Mail', 'ignore_inline_image',
                                                            self.imap_ignore_inline_image)

            self.tg_bot_token = self.get_config('Telegram', 'bot_token', self.tg_bot_token)
            if self.tg_bot_token:
                tool.mask_error_data.append(self.tg_bot_token)
            self.tg_forward_to_chat_id = self.get_config('Telegram', 'forward_to_chat_id',
                                                         self.tg_forward_to_chat_id, int)
            self.tg_message_thread_id = self.get_config('Telegram', 'message_thread_id',
                                                        self.tg_message_thread_id, int)
            self.tg_forward_mail_content = self.get_config('Telegram', 'forward_mail_content',
                                                           self.tg_forward_mail_content, bool)
            self.tg_prefer_html = self.get_config('Telegram', 'prefer_html', self.tg_prefer_html, bool)
            self.tg_markdown_version = self.get_config('Telegram', 'markdown_version', self.tg_markdown_version, int)
            self.tg_forward_attachment = self.get_config('Telegram', 'forward_attachment',
                                                         self.tg_forward_attachment, bool)
            self.tg_forward_embedded_images = self.get_config('Telegram', 'forward_embedded_images',
                                                              self.tg_forward_embedded_images, bool)

            self.tg_connection_read_timeout = self.get_config('Telegram', 'connection_read_timeout',
                                                              self.tg_connection_read_timeout, int)
            self.tg_connection_write_timeout = self.get_config('Telegram', 'connection_write_timeout',
                                                              self.tg_connection_write_timeout, int)
            self.tg_connection_connect_timeout = self.get_config('Telegram', 'connection_connect_timeout',
                                                              self.tg_connection_connect_timeout, int)
            self.tg_connection_pool_timeout = self.get_config('Telegram', 'connection_pool_timeout',
                                                              self.tg_connection_pool_timeout, int)
            self.tg_connection_pool_size = self.get_config('Telegram', 'connection_pool_size',
                                                              self.tg_connection_pool_size, int)

            if cmd_args.read_old_mails:
                self.imap_read_old_mails = True

            # Load filter settings
            self.filter_mode = self.get_config('Filters', 'mode', self.filter_mode)
            self.filter_whitelist_keywords = self._parse_list(
                self.get_config('Filters', 'whitelist_keywords', ''))
            self.filter_blacklist_keywords = self._parse_list(
                self.get_config('Filters', 'blacklist_keywords', ''))
            self.filter_whitelist_authors = self._parse_list(
                self.get_config('Filters', 'whitelist_authors', ''))
            self.filter_blacklist_authors = self._parse_list(
                self.get_config('Filters', 'blacklist_authors', ''))
            
            if self.filter_mode != 'disabled':
                logging.info("🔍 Filter mode: %s" % self.filter_mode)
                if self.filter_whitelist_keywords:
                    logging.info("   ✅ Whitelist keywords: %s" % ', '.join(self.filter_whitelist_keywords))
                if self.filter_blacklist_keywords:
                    logging.info("   ❌ Blacklist keywords: %s" % ', '.join(self.filter_blacklist_keywords))
                if self.filter_whitelist_authors:
                    logging.info("   ✅ Whitelist authors: %s" % ', '.join(self.filter_whitelist_authors))
                if self.filter_blacklist_authors:
                    logging.info("   ❌ Blacklist authors: %s" % ', '.join(self.filter_blacklist_authors))

        except configparser.ParsingError as parse_error:
            logging.critical(
                "Error parsing config file: Impossible to parse file %s. Message: %s"
                % (parse_error.source, parse_error.message)
            )
            sys.exit(2)
        except configparser.Error as config_error:
            logging.critical("Error parsing config file: %s." % config_error.message)
            sys.exit(2)

    def get_config(self, section, key, default=None, value_type=None):
        value = default
        try:
            if self.config_parser.has_section(section):
                if self.config_parser.has_option(section, key):
                    # get value based on type of default value
                    if value_type is int:
                        value = self.config_parser.getint(section, key)
                    elif value_type is float:
                        value = self.config_parser.getfloat(section, key)
                    elif value_type is bool:
                        value = self.config_parser.getboolean(section, key)
                    else:
                        # use string as default
                        value = self.config_parser.get(section, key)
            else:
                # Only raise for mandatory sections (Mail + Telegram)
                if section in ('Mail', 'Telegram'):
                    logging.warning("Get config value error for '%s'.'%s' (default: '%s'): Missing section '%s'."
                                    % (section, key, default, section))
                    raise configparser.NoSectionError(section)
                # For optional sections like Filters, just return default

        except configparser.Error as config_error:
            logging.critical("Error parsing config file: %s." % config_error.message)
            raise config_error
        except Exception as get_val_error:
            logging.critical(
                "Get config value error for '%s'.'%s' (default: '%s'): %s."
                % (section, key, default, get_val_error)
            )
            raise get_val_error

        return value

    @staticmethod
    def _parse_list(value: str) -> list:
        """Parse comma-separated string into list of lowercase trimmed items."""
        if not value or not value.strip():
            return []
        return [item.strip().lower() for item in value.split(',') if item.strip()]


class MailAttachmentType(Enum):
    BINARY = 1
    IMAGE = 2


class MailAttachment:
    idx: int = 0
    id: str = ''
    name: str = ''
    alt: str = ''
    type: MailAttachmentType = MailAttachmentType.BINARY
    file: bytes | None = None
    tg_id: str | None = None

    def __init__(self, attachment_type: MailAttachmentType = MailAttachmentType.BINARY):
        super().__init__()
        self.type = attachment_type

    def set_name(self, file_name: str):
        name: str = ''
        for file_name_part in email.header.decode_header(file_name):
            part, encoding = file_name_part
            name += Tool.binary_to_string(part, encoding=encoding)
        self.name = name

    def set_id(self, attachment_id: str):
        self.id = re.sub(r'[<>]', '', attachment_id)

    def get_title(self):
        if self.alt:
            return self.alt
        elif self.name:
            return self.name
        else:
            return self.file


class MailDataType(Enum):
    TEXT = 1
    HTML = 2


class MailImage(dict):
    key: str
    image: MailAttachment


class MailBody:
    text: str = ''
    html: str = ''
    images: list[MailImage]
    attachments: list[MailAttachment]

    def __init__(self):
        self.images = []
        self.attachments = []


class MailData:
    uid: str = ''
    raw: email.message.Message | None = None
    type: MailDataType = MailDataType.TEXT
    summary: str = ''
    mail_from: str = ''
    mail_subject: str = ''
    mail_body: str = ''
    mail_images: list[MailImage]
    attachment_summary: str = ''
    attachments: list[MailAttachment]

    def __init__(self):
        self.mail_images = []
        self.attachments = []


class TelegramBot:
    config: Config
    request: HTTPXRequest
    bot: Bot
    error_send_message: str = "Failed to send Telegram message: %s"

    def __init__(self, config: Config):
        self.config = config
        self.request = HTTPXRequest(connection_pool_size=8,
                                    read_timeout=config.tg_connection_read_timeout,
                                    write_timeout=config.tg_connection_write_timeout,
                                    connect_timeout=config.tg_connection_connect_timeout,
                                    pool_timeout=config.tg_connection_pool_timeout)
        self.bot = Bot(token=self.config.tg_bot_token, request=self.request)

    @staticmethod
    def cleanup_html(message: str, images: list[MailImage] | None = None, ignore_image_pattern: str = '') -> str:
        """
        Parse HTML message and remove HTML elements not supported by Telegram
        """
        # supported tags
        # https://core.telegram.org/bots/api#sendmessage
        # <b>bold</b>, <strong>bold</strong>
        # <i>italic</i>, <em>italic</em>
        # <u>underline</u>, <ins>underline</ins>
        # <s>strikethrough</s>, <strike>strikethrough</strike>, <del>strikethrough</del>
        # <b>bold <i>italic bold <s>italic bold strikethrough</s> <u>underline italic bold</u></i> bold</b>
        # <a href="http://www.example.com/">inline URL</a>
        # <a href="tg://user?id=123456789">inline mention of a user</a>
        # <code>inline fixed-width code</code>
        # <pre>pre-formatted fixed-width code block</pre>
        # <pre><code class="language-python">pre-formatted fixed-width code block
        #      written in the Python programming language</code></pre>
        # span elements only supported as spoiler elements
        # tg_msg = re.sub(r'<\s*span\b', '<span class="tg-spoiler"', tg_msg,
        #                 flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

        tg_body: str = message
        tg_msg: str = ''
        try:
            soup = BeautifulSoup(tg_body, 'html.parser')

            # remove all HTML comments safely using BS4
            for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
                comment.extract()

            # extract HTML body
            if soup.body:
                # Use decode_contents() to get inner HTML of body
                tg_body = soup.body.decode_contents()
            else:
                tg_body = soup.decode_contents()

            # remove control chars but preserve newlines and tabs
            tg_body = "".join(ch for ch in tg_body if unicodedata.category(ch)[0] != "C" or ch in ('\n', '\r', '\t'))

            # handle inline images
            image_seen: dict[str] = {}
            for match in re.finditer(
                    r'(?P<img><\s*img\s+[^>]*?\s*src\s*=\s*"'
                    r'(?P<src>(?P<proto>(cid|https?)):/*(?P<cid>[^"]*))"[^>]*?/?\s*>)',
                    tg_body,
                    flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE)):
                img: str = match.group('img')
                proto: str = match.group('proto')

                # extract alt or title value
                alt: str = re.sub(r'^.*?((title|alt)\s*=\s*"(?P<alt>[^"]+)")?.*$', r'\g<alt>',
                                  img, flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

                if 'http' in proto:
                    # web link
                    src = match.group('src')
                    if ignore_image_pattern and re.search(ignore_image_pattern, src, re.IGNORECASE):
                        continue
                    tg_body = tg_body.replace(img, "${img-link:%s|%s}" % (src, alt))
                else:
                    # attached/embedded image
                    cid = match.group('cid')
                    if cid == '' or cid in image_seen:
                        continue
                    image_seen[cid] = True

                    if images and cid in [i['key'] for i in images]:
                        # add image reference
                        tg_body = tg_body.replace(img, '${file:%s}' % cid)
                        # extract alt/title attributes of img elements
                        for image in images:
                            if image['key'] == cid:
                                image['image'].alt = alt
                                break
                    else:
                        # no file found, use alt text
                        tg_body = tg_body.replace(img, alt)

            # use alt text for all images without cid (embedded image)
            tg_body = re.sub(r'<\s*img\s+[^>]*?((title|alt)\s*=\s*"(?P<alt>[^"]+)")?[^>]*?/?\s*>', r'\g<alt>',
                             tg_body, flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

            # remove multiple line breaks and spaces (regular Browser logic)
            tg_body = re.sub(r'\s\s+', ' ', tg_body).strip()

            # remove attributes from elements but href of "a"- elements
            tg_msg = re.sub(r'<\s*?(?P<elem>\w+)\b\s*?[^>]*?(?P<ref>\s+href\s*=\s*(?P<q>[\'"]?)[^>]+?(?P=q))?[^>]*?>',
                            r'<\g<elem>\g<ref>>', tg_body, flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

            # remove style and script elements/blocks
            tg_msg = re.sub(r'<\s*(?P<elem>script|style)\s*>.*?</\s*(?P=elem)\s*>',
                            '', tg_msg, flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

            # translate paragraphs and line breaks (block elements)
            tg_msg = re.sub(r'</?\s*(?P<elem>(p|div|table|h\d+))\s*>', '\n', tg_msg,
                            flags=(re.MULTILINE | re.IGNORECASE))
            tg_msg = re.sub(r'</\s*(?P<elem>(tr))\s*>', '\n', tg_msg, flags=(re.MULTILINE | re.IGNORECASE))
            tg_msg = re.sub(r'</?\s*(br)\s*[^>]*>', '\n', tg_msg, flags=(re.MULTILINE | re.IGNORECASE))

            # prepare list items (migrate list items to "- <text of li element>")
            tg_msg = re.sub(r'(<\s*[ou]l\s*>[^<]*)?<\s*li\s*>', '\n- ', tg_msg, flags=(re.MULTILINE | re.IGNORECASE))
            tg_msg = re.sub(r'</\s*li\s*>([^<]*</\s*[ou]l\s*>)?', '\n', tg_msg, flags=(re.MULTILINE | re.IGNORECASE))

            # remove unsupported tags
            # https://core.telegram.org/api/entities
            # Allow 'blockquote' for expandable quotes
            regex_filter_elem = re.compile(
                r'<\s*(?!/?\s*(?P<elem>bold|strong|i|em|u|ins|s|strike|del|b|a|code|pre|blockquote)\b)[^>]*>',
                flags=(re.MULTILINE | re.IGNORECASE))
            tg_msg = re.sub(regex_filter_elem, '', tg_msg)

            # --- Logic to collapse quoted history ---
            # Try to find common reply headers and wrap the rest in <blockquote expandable>
            # 1. English: On ... wrote:
            # 2. Russian: ... пишет: / ... написал:
            # 3. Headers: From: ... Sent: ...
            
            # Use a unique marker to avoid regex complexity with HTML tags
            quote_marker = "||QUOTE_START||"
            
            # Regex for "On ... wrote:" (handling potential newlines/HTML artifacts)
            tg_msg = re.sub(r'(?i)(\n\s*On\s.+?wrote:\s*)', f'\n{quote_marker}\\1', tg_msg, count=1)
            
            # Regex for Russian "... пишет:" or "... написал:"
            if quote_marker not in tg_msg: # Only if not already found
                 tg_msg = re.sub(r'(?i)(\n\s*.+?(?:пишет|написал):\s*)', f'\n{quote_marker}\\1', tg_msg, count=1)
            
            # Regex for Outlook/Forward style "From: ... Sent: ..." (needs to match start of line)
            if quote_marker not in tg_msg:
                 tg_msg = re.sub(r'(?i)(\n\s*From:\s.+?Sent:\s.+)', f'\n{quote_marker}\\1', tg_msg, count=1)

            # If marker found, split and wrap
            if quote_marker in tg_msg:
                parts = tg_msg.split(quote_marker, 1)
                
                # Clean up the quoted part
                quote_content = parts[1].strip()
                
                # 1. Replace image placeholders inline with their names (not the raw CID)
                # For ${file:cid} - we need to find actual filename from images dict
                def replace_file_placeholder(match):
                    cid = match.group(1)
                    # Try to find filename from images dict passed to this function
                    if images:
                        for img in images:
                            if img['key'] == cid:
                                name = img['image'].name or 'image'
                                return f'[📷 {name}]'
                    return '[📷 Изображение]'
                
                quote_content = re.sub(r'\$\{file:([^}]+)\}', replace_file_placeholder, quote_content)
                
                # For ${img-link:url|alt} - use alt text or generic
                def replace_link_placeholder(match):
                    alt = match.group(1).strip()
                    return f'[📷 {alt}]' if alt else '[� Изображение]'
                
                quote_content = re.sub(r'\$\{img-link:[^|]*\|([^}]*)\}', replace_link_placeholder, quote_content)

                # 2. Convert standard quote markers (>) to simple text or remove
                quote_content = re.sub(r'^\s*>\s*', '', quote_content, flags=re.MULTILINE)
                
                # 3. Compact newlines in history
                quote_content = re.sub(r'\n\s*\n', '\n', quote_content)

                # 4. Inject "Beautiful Separators" and Clean Headers
                # Separator line: ━━━━━━━━━━━━━━━━
                separator = "\n\n<b>━━━━━━━━━━━━━━━━</b>\n"

                # Helper to parse and shift date
                def reformat_header(match):
                    content = match.group('content')
                    # Try to parse English format: Wed, Jan 7, 2026 at 7:35 PM
                    try:
                        # Normalize spaces
                        clean_content = re.sub(r'\s+', ' ', content).strip()
                        # Extract date part if mixed with name (simple heuristic: generic split or regex?)
                        # Actually 'content' in our regex is usually "Date Name" or "Day, Date at Time Name"
                        # It is hard to separate Name from Date perfectly without strict structure.
                        # But for "On [Date] [Name]", the date usually comes first.
                        
                        # Let's try to extract a date-like string from the start
                        # Regex for "Wed, Jan 7, 2026 at 7:35 PM"
                        date_match = re.search(r'(?P<date>\w{3}, \w{3} \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M)', clean_content)
                        if date_match:
                            date_str = date_match.group('date')
                            # Parse
                            dt = datetime.strptime(date_str, "%a, %b %d, %Y at %I:%M %p")
                            # Shift: User Input (UTC+5) -> MSK (UTC+3) => -2 hours
                            dt_msk = dt - timedelta(hours=2)
                            formatted_date = dt_msk.strftime('%d.%m.%Y %H:%M MSK')
                            
                            # Replace the date part in the content string with the new formatted date
                            new_content = clean_content.replace(date_str, formatted_date)
                            return f'{separator}<b>{new_content}</b>\n'
                    except Exception:
                        pass # Fallback to original
                    
                    return f'{separator}<b>{content}</b>\n'

                def reformat_first_header(match):
                    # Same as above but without separator
                    text = reformat_first(match)
                    return text.replace(separator, '')
                
                def reformat_first(match):
                     # Wrapper to re-use reformat_header logic but returning no separator if called directly is hard 
                     # because 'separator' is in reformat_header string.
                     # Let's simple copy-paste logic or use a flag? 
                     # Simplify: call reformat_header and strip leading separator.
                     res = reformat_header(match)
                     if res.startswith(separator):
                         res = res[len(separator):]
                     return res

                # Highlight and separate English headers
                # Regex matches: On (Group: Content) (Optional: Email) (Optional: Newline) wrote:
                quote_content = re.sub(
                    r'(?i)\n\s*On\s+(?P<content>.+?)\s*(?:(?:<|&lt;)[^>]+?(?:>|&gt;))?\s*(?:\n\s*)?wrote:\s*',
                    reformat_header, 
                    quote_content, 
                    flags=re.DOTALL
                )

                # Highlight and separate Russian headers
                quote_content = re.sub(
                    r'(?i)\n\s*(?P<content>.+?)\s*(?:(?:<|&lt;)[^>]+?(?:>|&gt;))?\s*(?:\n\s*)?(?:пишет|написал):\s*',
                    f'{separator}<b>\\g<content></b>\n',
                    quote_content,
                    flags=re.DOTALL
                )
                
                # Highlight and separate Outlook/Forward headers
                # Pattern: From: [Name] [newline] Sent: [Date] ...
                # Keep as is but bold/separate (hard to partial parse reliably without losing info)
                quote_content = re.sub(r'(?i)(\n\s*From:\s.+?Sent:\s.+)', 
                                       f'{separator}<b>\\1</b>', quote_content)

                # 1. Format the FIRST header (start of quote)
                quote_content = re.sub(
                    r'(?si)^\s*On\s+(?P<content>.+?)\s*(?:(?:<|&lt;)[^>]+?(?:>|&gt;))?\s*(?:\n\s*)?wrote:\s*',
                    reformat_first,
                    quote_content, count=1
                )
                quote_content = re.sub(
                    r'(?si)^\s*(?P<content>.+?)\s*(?:(?:<|&lt;)[^>]+?(?:>|&gt;))?\s*(?:\n\s*)?(?:пишет|написал):\s*',
                    f'<b>\\g<content></b>\n',
                    quote_content, count=1
                )

                # Ensure we close the quote
                # Insert a placeholder for a blank line that survives the whitespace cleanup
                tg_msg = f"{parts[0]}||QUOTE_SEP||<blockquote expandable>{quote_content}</blockquote>"
            # ----------------------------------------

            # remove empty links
            tg_msg = re.sub(r'<\s*a\s*>(?P<link>[^<]*)</\s*a\s*>', r'\g<link> ', tg_msg,
                            flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

            # remove links without text (tracking stuff, and none clickable)
            tg_msg = re.sub(r'<\s*a\s*[^>]*>\s*</\s*a\s*>', ' ', tg_msg,
                            flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE))

            # remove empty elements
            tg_msg = re.sub(r'<\s*\w\s*>\s*</\s*\w\s*>', ' ', tg_msg, flags=(re.DOTALL | re.MULTILINE))

            # remove multiple line breaks
            tg_msg = re.sub(r'\s*[\r\n](\s*[\r\n])+', "\n", tg_msg)

            # preserve NBSPs
            tg_msg = re.sub(r'&nbsp;', ' ', tg_msg, flags=re.IGNORECASE)
            
            # Restore the blank line before the quote
            tg_msg = tg_msg.replace('||QUOTE_SEP||', '\n')

        except Exception as ex:
            logging.critical("Error cleaning HTML: %s" % str(ex))

        return tg_msg

    async def send_message(self, mails: list[MailData]):
        """
        Send mail data over Telegram API to chat/user.
        """
        try:
            # Initialize the Bot with HTTPXRequest with increased connection pool size and proper timeouts
            # async with Bot(token=self.config.tg_bot_token, request=self.request) as bot:
            logging.debug("Bot initialized, connecting to Chat '{0}'...".format(self.config.tg_forward_to_chat_id))
            bot = self.bot
            if bot:
                tg_chat_title = str(self.config.tg_forward_to_chat_id)
                try:
                    tg_chat: ChatFullInfo = await bot.get_chat(self.config.tg_forward_to_chat_id)
                    # get chat title
                    tg_chat_title = tg_chat.full_name
                    if not tg_chat_title:
                        tg_chat_title = tg_chat.title
                    if not tg_chat_title:
                        tg_chat_title = str(tg_chat.id)
                except Exception as chat_error:
                    logging.warning("Unknown Chat Title (Connection/Permission error): %s" % str(chat_error))

                for mail in mails:
                    try:
                        if self.config.tg_markdown_version == 2:
                            parser = ParseMode.MARKDOWN_V2
                        else:
                            parser = ParseMode.MARKDOWN
                        if mail.type == MailDataType.HTML:
                            parser = ParseMode.HTML

                        if self.config.tg_forward_mail_content or not self.config.tg_forward_attachment:
                            # send mail content (summary)
                            message = mail.summary

                            # upload images (only those still referenced in main text, not quoted)
                            image_no = 1
                            for mail_image in mail.mail_images:
                                # image = mail.mail_images.[image_id]
                                image = mail_image['image']
                                placeholder = '${file:%s}' % image.id
                                
                                # Skip if this image's placeholder was already removed (it was in quoted content)
                                if placeholder not in message:
                                    continue
                                
                                title = image.get_title() or f'image_{image_no}'

                                if self.config.tg_forward_embedded_images:
                                    # Simple caption: just the filename
                                    caption = title
                                    if len(caption) > 200:
                                        caption = caption[:197] + "..."

                                    photo_kwargs = {
                                        'chat_id': self.config.tg_forward_to_chat_id,
                                        'parse_mode': parser,
                                        'caption': caption,
                                        'photo': image.file,
                                    }
                                    if self.config.tg_message_thread_id:
                                        photo_kwargs['message_thread_id'] = self.config.tg_message_thread_id

                                    doc_message: Message = await self.bot.send_photo(**photo_kwargs)
                                    photo_size: list[PhotoSize] = doc_message.photo
                                    image.tg_id = photo_size[0].file_id

                                # Remove the placeholder from text (image already sent separately)
                                message = message.replace(placeholder, '')
                                image_no += 1

                            # Replace external image links with clickable placeholder
                            for img_link in re.finditer(r'(\${img-link:(?P<src>[^|]*)\|(?P<alt>[^}]*)})', message,
                                                        flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE)):
                                src = img_link.group('src')
                                alt = img_link.group('alt').strip()
                                # Use alt text if available, otherwise generic placeholder
                                link_text = alt if alt else '[Изображение]'
                                message = message.replace(img_link.groups()[0], 
                                                          '<a href="%s">%s</a>' % (src, link_text))
                            
                            # Clean up extra whitespace left after removing image placeholders
                            message = re.sub(r'\n{3,}', '\n\n', message)  # Max 1 empty line
                            message = re.sub(r'(\n\n)+(\n*<b>Subject)', r'\n\n\2', message)  # Clean before Subject
                            # Prepare arguments (filter out None values)
                            send_kwargs = {
                                'chat_id': self.config.tg_forward_to_chat_id,
                                'parse_mode': parser,
                                'disable_web_page_preview': True
                            }
                            if self.config.tg_message_thread_id:
                                send_kwargs['message_thread_id'] = self.config.tg_message_thread_id

                            logging.debug("Preparing to send text message to '%s'..." % self.config.tg_forward_to_chat_id)
                            tg_message: Message = await self.bot.send_message(text=message, **send_kwargs)
                            logging.debug("Text message sent successfully. Msg ID: %s" % tg_message.message_id)

                            logging.info("📤 Sent: [%s] %s -> %s (ID: %s)"
                                         % (mail.uid, mail.mail_subject,
                                            tg_chat_title, tg_message.message_id))

                        if self.config.tg_forward_attachment and len(mail.attachments) > 0:
                            for attachment in mail.attachments:
                                subject = mail.mail_subject
                                if mail.type == MailDataType.HTML:
                                    file_name = attachment.name
                                    caption = '<b>' + subject + '</b>:\n' + file_name
                                else:
                                    file_name = escape_markdown(
                                        text=attachment.name, version=self.config.tg_markdown_version)
                                    caption = '*' + subject + '*:\n' + file_name

                                if len(caption) > 1024:
                                    caption = caption[:1021] + "..."

                                # Prepare arguments for document
                                doc_kwargs = {
                                    'chat_id': self.config.tg_forward_to_chat_id,
                                    'parse_mode': parser,
                                    'caption': caption,
                                    'document': attachment.file,
                                    'filename': attachment.name,
                                    'disable_content_type_detection': False
                                }
                                if self.config.tg_message_thread_id:
                                    doc_kwargs['message_thread_id'] = self.config.tg_message_thread_id

                                tg_message = await self.bot.send_document(**doc_kwargs)

                                logging.info("Attachment '%s' was sent with ID '%i' to '%s' (ID: '%s')"
                                             % (attachment.name, tg_message.message_id,
                                                tg_chat_title, str(self.config.tg_forward_to_chat_id)))

                    except error.TelegramError as tg_mail_error:
                        msg = "❌ Failed to send Telegram message (UID: %s) to '%s': %s" \
                              % (mail.uid, str(self.config.tg_forward_to_chat_id), tg_mail_error.message)
                        logging.critical(msg)
                        try:
                            # try to send error via telegram, and ignore further errors
                            await self.bot.send_message(chat_id=self.config.tg_forward_to_chat_id,
                                                        parse_mode=ParseMode.MARKDOWN_V2,
                                                        text=escape_markdown(msg, version=2),
                                                        disable_web_page_preview=False)
                        except Exception:
                            logging.critical("Failed to send error message {0}".format(tg_mail_error.message))

                    except Exception as send_mail_error:
                        error_msgs = [self.config.tool.binary_to_string(arg) for arg in send_mail_error.args]
                        msg = "Failed to send Telegram message (UID: %s) to '%s': %s" \
                              % (mail.uid, str(self.config.tg_forward_to_chat_id), ', '.join(error_msgs))
                        logging.critical(msg)
                        try:
                            # try to send error via telegram, and ignore further errors
                            await self.bot.send_message(chat_id=self.config.tg_forward_to_chat_id,
                                                        parse_mode=ParseMode.MARKDOWN_V2,
                                                        text=escape_markdown(msg, version=2),
                                                        disable_web_page_preview=False)
                        except Exception:
                            logging.critical("Failed to send error message {0}".format("".join(error_msgs)))

        except error.TelegramError as tg_error:
            logging.critical(self.error_send_message % tg_error.message)
            return False

        except Exception as send_error:
            error_msgs = [self.config.tool.binary_to_string(arg) for arg in send_error.args]
            logging.critical(self.error_send_message % ', '.join(error_msgs))
            return False

        return True


class Mail:
    mailbox: typing.Optional[imaplib2.IMAP4_SSL] = None
    config: Config
    last_uid: str = ''

    previous_error = None

    class MailError(Exception):
        def __init__(self, message, errors=None):
            super().__init__(message)
            self.errors = errors

    def __init__(self, config):
        """
        Login to remote IMAP server.
        """
        self.config = config
        try:
            self.mailbox = imaplib2.IMAP4_SSL(host=config.imap_server,
                                              port=config.imap_port,
                                              timeout=config.imap_timeout)
            rv, _ = self.mailbox.login(config.imap_user, config.imap_password)
            if rv != 'OK':
                msg = "Cannot login to mailbox: %s" % str(rv)
                raise self.MailError(msg)

        except socket.gaierror as gai_error:
            msg = "Connection error '%s:%i': %s" % (config.imap_server,
                                                    config.imap_port,
                                                    gai_error.strerror)
            logging.debug(msg)
            raise self.MailError(msg, gai_error)

        except imaplib2.IMAP4_SSL.error as imap_ssl_error:
            error_msgs = [self.config.tool.binary_to_string(arg) for arg in imap_ssl_error.args]
            msg = "Login to '%s:%i' failed: %s" % (config.imap_server,
                                                   config.imap_port,
                                                   ', '.join(error_msgs))
            logging.debug(msg)
            raise self.MailError(msg, imap_ssl_error)

        except Exception as login_error:
            msg = "Mail error during connection to '%s:%i' failed: %s" \
                  % (config.imap_server, config.imap_port, ', '.join(map(str, login_error.args)))
            logging.debug(msg)
            raise self.MailError(msg, login_error)

        rv, mailboxes = self.mailbox.list()
        if rv != 'OK':
            self.disconnect()
            msg = "Can't get list of available mailboxes / folders: %s" % str(rv)
            raise self.MailError(msg)
        else:

            logging.info("📁 Available Mailboxes:")
            for mb in mailboxes:
                try:
                    # mb is bytes, e.g. b'() "/" "Archive"'
                    mb_str = mb.decode('utf-8')
                    # Extract name: usually inside quotes at the end
                    match = re.search(r'"([^"]+)"$', mb_str)
                    if match:
                        name = match.group(1)
                    else:
                        # Fallback: take last part
                        name = mb_str.strip().split()[-1]
                    logging.info("   • %s" % name)
                except Exception:
                    logging.info("   • %s" % mb.decode('utf-8', errors='ignore'))

        rv, _ = self.mailbox.select(config.imap_folder)
        if rv == 'OK':
            logging.info("Processing mailbox...")
        else:
            msg = "ERROR: Unable to open mailbox: %s" % str(rv)
            logging.debug(msg)
            raise self.MailError(msg)

    def is_connected(self):
        if self.mailbox is not None:
            try:
                rv, _ = self.mailbox.noop()
                if rv == 'OK':
                    logging.debug("Connection is working...")
                    return True
            except Exception as connection_check_error:
                msg = "Error during connection check [noop]: %s" \
                      % (', '.join(map(str, connection_check_error.args)))
                logging.error(msg)
        return False

    def disconnect(self):
        if self.mailbox is not None:
            try:
                self.mailbox.close()
                self.mailbox.logout()
            except Exception as ex:
                logging.debug("Cannot close mailbox: %s" % ', '.join(map(str, ex.args)))
            finally:
                self.mailbox = None

    def filter_mail(self, mail: MailData) -> bool:
        """
        Apply filters to mail. Returns True if mail should be forwarded.
        """
        mode = self.config.filter_mode.lower()
        
        if mode == 'disabled':
            return True
        
        # Prepare search text (subject + body + from)
        search_text = (mail.mail_subject + ' ' + mail.mail_body).lower()
        from_addr = mail.mail_from.lower()
        
        # Check whitelist
        def matches_whitelist():
            for kw in self.config.filter_whitelist_keywords:
                if kw in search_text:
                    return True
            for author in self.config.filter_whitelist_authors:
                if author in from_addr:
                    return True
            return False
        
        # Check blacklist
        def matches_blacklist():
            for kw in self.config.filter_blacklist_keywords:
                if kw in search_text:
                    return True
            for author in self.config.filter_blacklist_authors:
                if author in from_addr:
                    return True
            return False
        
        if mode == 'whitelist':
            # Only forward if matches whitelist
            return matches_whitelist()
        
        elif mode == 'blacklist':
            # Forward unless matches blacklist
            return not matches_blacklist()
        
        elif mode == 'combined':
            # If whitelist is defined, must match it. If empty, allow all.
            # Then check blacklist - if matches, block.
            has_whitelist = bool(self.config.filter_whitelist_keywords or 
                                 self.config.filter_whitelist_authors)
            if has_whitelist and not matches_whitelist():
                return False
            if matches_blacklist():
                return False
            return True
        
        # Unknown mode - default to forward
        logging.warning("Unknown filter mode: %s" % mode)
        return True

    @staticmethod
    def decode_body(msg) -> MailBody:
        """
        Get payload from message and return structured body data
        """
        html_part = None
        text_part = None
        attachments: list[MailAttachment] = []
        images: list[MailImage] = []
        index: int = 1

        for part in msg.walk():
            if part.get_content_type().startswith('multipart/'):
                continue

            elif part.get_content_type() == 'text/plain':
                # extract plain text body
                text_part = part.get_payload(decode=True)
                encoding = part.get_content_charset()
                if not encoding:
                    encoding = 'utf-8'
                if text_part:
                    text_part = bytes(text_part).decode(encoding, errors='replace').strip()

            elif part.get_content_type() == 'text/html':
                # extract HTML body
                html_part = part.get_payload(decode=True)
                encoding = part.get_content_charset()
                if not encoding:
                    encoding = 'utf-8'
                if html_part:
                    html_part = bytes(html_part).decode(encoding, errors='replace').strip()

            elif part.get_content_type() == 'message/rfc822':
                continue

            elif part.get_content_type() == 'text/calendar':
                # extract calendar/appointment files
                attachment = MailAttachment()
                attachment.idx = index
                attachment.name = 'invite.ics'
                attachment.file = part.get_payload(decode=True)
                attachments.append(attachment)
                index += 1

            elif part.get_content_charset() is None:
                if part.get_content_disposition() == 'attachment':
                    # extract attachments
                    attachment = MailAttachment()
                    attachment.idx = index
                    filename = part.get_filename()
                    attachment.set_name(str(filename) if filename else "unknown_attachment")
                    attachment.file = part.get_payload(decode=True)
                    attachments.append(attachment)
                    index += 1

                elif part.get_content_disposition() == 'inline':
                    # extract inline images
                    if part.get_content_type() in ('image/png', 'image/jpeg', 'image/gif'):
                        image_data = part.get_payload(decode=True)
                        if not image_data:
                            continue
                        
                        image = MailAttachment(MailAttachmentType.IMAGE)
                        image.idx = index
                        filename = part.get_filename()
                        image.set_name(str(filename) if filename else "unnamed.jpg")
                        image.set_id(part.get('Content-ID', image.name))
                        image.file = image_data
                        images.append(MailImage(key=image.id, image=image))
                        index += 1

        body = MailBody()
        body.text = text_part or ''
        body.html = html_part or ''
        body.attachments = attachments
        body.images = images
        return body

    def get_last_uid(self) -> str:
        """
        get UID of most recent mail
        """
        rv, data = self.mailbox.uid('search', None, 'ALL')
        if rv != 'OK' or not data[0]:
            logging.debug("No messages found!")
            return '0'
        return self.config.tool.binary_to_string(data[0].split()[-1])

    def parse_mail(self, uid, mail) -> (MailData | None):
        """
        parse data from mail like subject, body and attachments and return structured mail data
        """
        try:
            msg: email.message.Message[str, str] = email.message_from_bytes(mail)

            # decode body data (text, html, multipart/attachments)
            body = self.decode_body(msg)
            message_type = MailDataType.TEXT
            content = ''

            if self.config.tg_forward_mail_content:
                # remove useless content
                if body.text:
                    content = body.text.replace('()', '').replace('[]', '').strip()

                    # insert inline image
                    if self.config.tg_forward_embedded_images:
                        for cid_match in re.finditer(r'\[cid:([^]]*)]', content,
                                               flags=(re.DOTALL | re.MULTILINE | re.IGNORECASE)):
                            cid_value = cid_match.group(1)
                            for mail_image in body.images:
                                if mail_image['key'] == cid_value:
                                    content = content.replace(
                                        '[cid:' + cid_value + ']',
                                        '${file:' + mail_image['key'] + '}'
                                    )
                                    break

                if self.config.tg_prefer_html:
                    # Prefer HTML
                    if body.html:
                        message_type = MailDataType.HTML
                        content = TelegramBot.cleanup_html(body.html, body.images, self.config.imap_ignore_inline_image)

                    elif body.text:
                        content = escape_markdown(text=content,
                                                          version=self.config.tg_markdown_version)

                else:
                    if body.text:
                        content = escape_markdown(text=content,
                                                          version=self.config.tg_markdown_version)

                    elif body.html:
                        message_type = MailDataType.HTML
                        content = TelegramBot.cleanup_html(body.html, body.images, self.config.imap_ignore_inline_image)

                if content:
                    # remove multiple line breaks (keeping up to 1 empty line)
                    content = re.sub(r'(\s*\r?\n){2,}', "\n\n", content)

                    if message_type == MailDataType.HTML:
                        # add space after links (provide space for touch on link lists)
                        # '&lt;' keep mail marker together (ex.: &lt;<a href="mailto:t@ex.com">t@ex.xom</a>&gt;)
                        content = re.sub(r'(?P<a></a>(\s*&gt;)?)\s*', r'\g<a>\n\n', content, flags=re.MULTILINE)

                    # remove spaces and line breaks on start and end (enhanced strip)
                    content = re.sub(r'^\s*', '', content)
                    content = re.sub(r'\s*$', '', content)

                    max_len = self.config.imap_max_length
                    content_len = len(content)
                    if message_type == MailDataType.HTML and content_len > 0:
                        # get length of parsed HTML (all tags and masked images (ex.: '${<image>|<title>}') removed)
                        content_plain: str = re.sub(r'(<[^>]*>)|(\${[^}]+})', '', content, flags=re.MULTILINE)
                        # get new max length based on plain text factor
                        plain_factor: float = (len(content_plain) / content_len) + float(1)
                        max_len = int(max_len * plain_factor)
                    if content_len > max_len:
                        content = content[:max_len]
                        if message_type == MailDataType.HTML:
                            # remove incomplete html tag
                            content = re.sub(r'<(\s*\w*(\s*[^>]*?)?(</[^>]*)?)?$', '', content)
                        else:
                            # remove last "\"
                            content = re.sub(r'\\*$', '', content)
                        content += "... (first " + str(max_len) + " characters)"

            # attachment summary
            attachments_summary = ""
            if body.attachments:
                if message_type == MailDataType.HTML:
                    attachments_summary = "\n\n" + chr(10133) + \
                                          " <b>" + str(len(body.attachments)) + " attachments:</b>\n"
                else:
                    attachments_summary = "\n\n" + chr(10133) + \
                                          " **" + str(len(body.attachments)) + " attachments:**\n"
                for attachment in body.attachments:
                    if message_type == MailDataType.HTML:
                        file_name = html.escape(attachment.name)
                    else:
                        file_name = escape_markdown(
                            text=attachment.name, version=self.config.tg_markdown_version)
                    attachments_summary += "\n " + str(attachment.idx) + ": " + file_name

            # subject
            subject = self.config.tool.decode_mail_data(msg['Subject'])
            if not subject or not subject.strip():
                subject = "—"
            elif re.match(r'^\s*(re|fwd|fw)[:\s]*$', subject, re.IGNORECASE):
                subject = subject.strip() + " —"

            # build summary
            mail_from = self.config.tool.decode_mail_data(msg['From'])

            if message_type == MailDataType.HTML:
                mail_from = html.escape(mail_from, quote=True)
                subject = html.escape(subject, quote=True)
                # Modern HTML Header
                email_text = f"<b>From:</b> {mail_from}\n<b>Subject:</b> {subject}\n\n"
            else:
                subject = escape_markdown(text=subject, version=self.config.tg_markdown_version)
                mail_from = escape_markdown(text=mail_from, version=self.config.tg_markdown_version)
                email_text = f"*From:* {mail_from}\n*Subject:* {subject}\n\n"
            
            email_text += content + " " + attachments_summary

            # Footer: Date
            try:
                mail_date_str = msg.get('Date')
                if mail_date_str:
                    dt = parsedate_to_datetime(mail_date_str)
                    # Helper for MSK timezone (UTC+3)
                    msk_tz = timezone(timedelta(hours=3))
                    dt_msk = dt.astimezone(msk_tz)
                    
                    # Format: 07.01.2026 13:28 MSK
                    formatted_date = dt_msk.strftime('%d.%m.%Y %H:%M MSK')
                    
                    if message_type == MailDataType.HTML:
                        email_text += f"\n\n📅 <b>{formatted_date}</b>"
                    else:
                        email_text += f"\n\n📅 *{formatted_date}*"
            except Exception as e:
                logging.warning(f"Failed to parse email date: {e}")

            # Final safety check for Telegram message length limit (4096 chars)
            if len(email_text) > 4096:
                email_text = email_text[:4093] + "..."

            mail_data = MailData()
            mail_data.uid = uid
            mail_data.raw = msg
            mail_data.type = message_type
            mail_data.mail_from = mail_from
            mail_data.mail_subject = subject
            mail_data.mail_body = content
            mail_data.mail_images = body.images
            mail_data.summary = email_text
            mail_data.attachment_summary = attachments_summary
            mail_data.attachments = body.attachments

            return mail_data

        except Exception as parse_error:
            if len(parse_error.args) > 0:
                logging.critical("Cannot parse mail: %s" % parse_error.args[0])
            else:
                logging.critical("Cannot parse mail: %s" % parse_error.__str__())
            return None

    def search_mails(self) -> list[MailData]:
        """
        Search mail on remote IMAP server and return list of parsed mails.
        """
        if self.last_uid is None or self.last_uid == '':
            self.last_uid = self.get_last_uid()
            logging.info("Most recent UID: '%s'" % self.last_uid)

        # build IMAP search string
        search_string = self.config.imap_search
        if not search_string:
            search_string = "(UID %s:* UNSEEN)" % str(self.last_uid)
        else:
            search_string = re.sub(r'\${lastUID}', str(self.last_uid), search_string, flags=re.IGNORECASE)

        if re.match(r'.*\bUID\b\s*:.*', search_string) and self.last_uid == '':
            # empty mailbox
            return []

        try:
            rv, data = self.mailbox.uid('search', None, search_string)
            if rv != 'OK' or not data[0]:
                logging.debug("No messages found!")
                return []

        except imaplib2.IMAP4_SSL.error as search_error:
            error_msgs = [self.config.tool.binary_to_string(arg) for arg in search_error.args]
            msg = "Search with '%s' returned: %s" % (search_string, ', '.join(error_msgs))
            if msg != self.previous_error:
                logging.error(msg)
            self.previous_error = msg
            self.disconnect()
            raise self.MailError(msg)

        except Exception as search_ex:
            msg = ', '.join(map(str, search_ex.args))
            logging.critical("Cannot search mail: %s" % msg)
            self.disconnect()
            raise self.MailError(msg)

        mails = []
        if self.config.imap_read_old_mails and not self.config.imap_read_old_mails_processed:
            # ignore current/max UID during first loop
            max_uid = ''
            # don't repeat this on next loops
            self.config.imap_read_old_mails = False
            logging.info("Ignore most recent UID '%s', as old mails have to be processed first..." % self.last_uid)
        else:
            max_uid = self.last_uid
            if not self.config.imap_read_old_mails_processed:
                self.config.imap_read_old_mails_processed = True
                logging.info("Reading mails having UID more recent than '%s', using search: '%s'"
                             % (self.last_uid, search_string))

        for cur_uid in sorted(data[0].split()):
            current_uid = self.config.tool.binary_to_string(cur_uid)

            try:
                # Check message size first to allow OOM protection (50MB limit)
                rv, size_data = self.mailbox.uid('fetch', cur_uid, '(RFC822.SIZE)')
                if rv == 'OK':
                    # Parse size: '123 (RFC822.SIZE 45678)' -> 45678
                    size_response = ''
                    for part in size_data:
                        if part and part != b')':
                            size_response += self.config.tool.binary_to_string(part)
                    match = re.search(r'RFC822\.SIZE\s+(\d+)', size_response)
                    if match:
                        size_bytes = int(match.group(1))
                        if size_bytes > 52428800:  # 50MB limit
                            logging.warning(
                                "Skipping mail UID '%s' (Size: %.2f MB) - exceeds 50MB limit" %
                                (current_uid, size_bytes / (1024 * 1024)))
                            continue

                rv, fetch_data = self.mailbox.uid('fetch', cur_uid, '(BODY[])')
                if rv != 'OK':
                    logging.error("ERROR getting message: %s" % current_uid)
                    return []

                msg_raw = None
                for response_part in fetch_data:
                    if isinstance(response_part, tuple):
                        msg_raw = response_part[1]
                        break

                if msg_raw is None:
                    # Log the raw data to debug finding issues
                    logging.error("Could not find message body in fetch response for UID '%s'. Data: %s" 
                                  % (current_uid, str(fetch_data)[:200]))
                    continue
                mail = self.parse_mail(current_uid, msg_raw)
                if mail is None:
                    logging.error("Can't parse mail with UID: '%s'" % current_uid)
                else:
                    # Apply filters
                    if self.filter_mail(mail):
                        logging.info("📥 Parsed: [%s] Subject: %s" % (current_uid, mail.mail_subject))
                        mails.append(mail)
                    else:
                        logging.debug("🚫 Filtered out: [%s] Subject: %s" % (current_uid, mail.mail_subject))

            except Exception as mail_error:
                logging.critical("Cannot process mail with UID '%s': %s" % (current_uid,
                                                                            ', '.join(map(str, mail_error.args))))

            finally:
                # remember new UID for next loop
                max_uid = current_uid

        if len(mails) > 0:
            self.last_uid = max_uid
            logging.info("🚀 Forwarding %i new email(s)... (Last UID: %s)" % (len(mails), self.last_uid))
        return mails


class SystemdHandler(logging.Handler):
    """
        Class to handle logging options.
    """
    PREFIX = {
        logging.CRITICAL: "🔥 CRITICAL: ",
        logging.ERROR:    "❌ ERROR:    ",
        logging.WARNING:  "⚠️  WARNING:  ",
        logging.INFO:     "ℹ️  INFO:     ",
        logging.DEBUG:    "🐛 DEBUG:    ",
        logging.NOTSET:   "❓ NOTSET:   ",
    }
    tool: Tool

    def __init__(self, stream=sys.stdout):
        self.stream = stream
        self.tool = None
        logging.Handler.__init__(self)

    def emit(self, record):
        try:
            if self.tool is not None:
                # Normalize message and replace sensitive data
                record.msg = self.tool.build_error_message(record.msg)
            msg = self.PREFIX[record.levelno] + self.format(record) + "\n"
            self.stream.write(msg)
            self.stream.flush()
        except Exception as emit_error:
            self.handleError(record)
            if len(emit_error.args) > 0:
                print("ERROR: SystemdHandler.emit failed with: " + emit_error.args[0])
            else:
                print("ERROR: SystemdHandler.emit failed with: " + emit_error.__str__())


async def main() -> None:
    """
        Run the main program
    """
    sys_handler = SystemdHandler()
    root_logger = logging.getLogger()
    root_logger.setLevel("INFO")
    root_logger.addHandler(sys_handler)
    
    # Suppress verbose HTTP logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.ExtBot").setLevel(logging.WARNING)


    args_parser = argparse.ArgumentParser(description='Mail to Telegram Forwarder')
    args_parser.add_argument('-c', '--config', type=str, help='Path to config file', required=True)
    args_parser.add_argument('-o', '--read-old-mails', action='store_true', required=False,
                             help='Read mails received, before application was started')
    cmd_args = args_parser.parse_args()

    if cmd_args.config is None:
        logging.warning("Could not load config file, as no config file was provided.")
        sys.exit(2)

    mailbox = None
    last_try = time.time()
    tool = Tool()
    sys_handler.tool = tool
    try:
        config = Config(tool, cmd_args)
        sys_handler.mask_error_data = tool.mask_error_data
        logging.info("Telegram Config Loaded: Chat ID='%s', Thread ID='%s'" 
                     % (config.tg_forward_to_chat_id, config.tg_message_thread_id))
        tg_bot = TelegramBot(config)
        mailbox = Mail(config)

        # Keep polling
        while True:
            try:
                if mailbox is None:
                    mailbox = Mail(config)
                else:
                    if not mailbox.is_connected():  # reconnect on error (broken connection)
                        if last_try + 60 < time.time():
                            mailbox = Mail(config)
                            if not mailbox.is_connected():
                                await asyncio.sleep(20)
                                continue
                            else:
                                last_try = time.time()  # new timeout on success
                        else:
                            await asyncio.sleep(20)
                            continue

                mails = mailbox.search_mails()

                if config.imap_disconnect:
                    # if not reuse previous connection
                    mailbox.disconnect()

                # send mail data via TG bot
                if mails is not None and len(mails) > 0:
                    await tg_bot.send_message(mails)

                if config.imap_push_mode:
                    logging.info("IMAP IDLE mode - Update")
                    try:
                        # Enter IDLE mode and wait for updates or refresh interval
                        mailbox.mailbox.idle(timeout=config.imap_refresh)
                    except Exception as idle_error:
                        logging.warning("IDLE failed, falling back to sleep: %s" % str(idle_error))
                        await asyncio.sleep(float(config.imap_refresh))
                else:
                    await asyncio.sleep(float(config.imap_refresh))

            except Mail.MailError as mail_ex:
                if len(mail_ex.args) > 0:
                    logging.critical('Error occurred [mail]: %s' % ', '.join(map(str, mail_ex.args)))
                else:
                    logging.critical('Error occurred [mail]: %s' % mail_ex.__str__())

                if mailbox is not None:
                    mailbox.disconnect()

                # ignore errors already handled by Mail- Class

            except Exception as loop_error:
                if len(loop_error.args) > 0:
                    logging.critical('Error occurred [loop]: %s' % ', '.join(map(str, loop_error.args)))
                else:
                    logging.critical('Error occurred [loop]: %s' % loop_error.__str__())

                if mailbox is not None:
                    mailbox.disconnect()

    except KeyboardInterrupt:
        logging.critical('Stopping user aborted with CTRL+C')

    except Mail.MailError:  # ignore errors already handled by Mail- Class
        pass

    except Exception as main_error:
        if len(main_error.args) > 0:
            logging.critical('Error occurred [main]: %s' % ', '.join(map(str, main_error.args)))
        else:
            logging.critical('Error occurred [main]: %s' % main_error.__str__())

    finally:
        if mailbox is not None:
            mailbox.disconnect()
        logging.info('Mail to Telegram Forwarder stopped!')


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):  # Ignore exception when Ctrl-C is pressed
        asyncio.run(main())
