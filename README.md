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
