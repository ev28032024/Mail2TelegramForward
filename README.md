# Mail to Telegram Forwarder

---

## ✨ Features

| Feature | Description |
|---------|-------------|
|  **IMAP Support** | Works with any IMAP-capable email provider |
|  **Real-time IDLE** | Push notifications via IMAP IDLE (no polling) |
|  **Attachments** | Forward files and embedded images |
|  **HTML Support** | Rich formatting preserved in Telegram messages |
|  **Secure** | Credentials masked in logs, no data stored |
|  **Docker Ready** | Systemd service or Docker deployment |
|  **Lightweight** | Single Python script, minimal dependencies |

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
# Copy and edit the config file
cp conf/mailToTelegramForwarder.conf myconfig.conf
nano myconfig.conf
```

### 3. Run

```bash
python mailToTelegramForwarder.py -c myconfig.conf
```

---

## 🐳 Deployment

### Systemd Service

```bash
# Install
sudo cp mailToTelegramForwarder.py /opt/mailToTelegramForwarder/
sudo cp conf/mailToTelegramForwarder.conf /etc/mail-to-telegram-forwarder/
sudo cp systemd/mail-to-telegram-forwarder@.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now mail-to-telegram-forwarder@mailToTelegramForwarder

# Check status
sudo systemctl status mail-to-telegram-forwarder@mailToTelegramForwarder
sudo journalctl -fu mail-to-telegram-forwarder@mailToTelegramForwarder
```

---

## 🔧 Advanced Configuration

### IMAP Search Filters

```ini
# All unread emails (default)
search: (UID ${lastUID}:* UNSEEN)

# Filter by sender
search: (UID ${lastUID}:* UNSEEN HEADER From "@company.com")

# Filter by subject
search: (UID ${lastUID}:* UNSEEN HEADER Subject "Alert")

# All emails (including read)
search: (UID ${lastUID}:*)
```

### Ignore Tracking Pixels

```ini
# Ignore common 1x1 tracking images
ignore_inline_image: (spacer\.gif|pixel\.png|tracking\.)
```