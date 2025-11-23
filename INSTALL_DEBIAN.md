# Installation Guide for Debian/Ubuntu

This guide covers installation on Debian 12+ and Ubuntu systems that use externally-managed Python environments.

## Prerequisites

1. Install required system packages:
```bash
sudo apt update
sudo apt install python3 python3-full python3-venv python3-pip
```

## Installation

### Option 1: Quick Setup (Recommended)

Run the automated setup script:

```bash
# Make the script executable
chmod +x setup_debian.sh

# Run the setup
bash setup_debian.sh
```

This will:
- Create a virtual environment in `venv/`
- Install all dependencies from `requirements.txt`
- Set up the application to run

### Option 2: Manual Setup

If you prefer to set up manually:

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

## Running the Application

### Option 1: Using the run script

```bash
# Make the script executable (first time only)
chmod +x run_debian.sh

# Run the application
bash run_debian.sh
```

### Option 2: Manual run

```bash
# Activate virtual environment
source venv/bin/activate

# Run the application
python main.py

# When done, deactivate the virtual environment
deactivate
```

## Configuration

1. Copy the example environment file (if provided) or create `.env`:
```bash
cp .env.example .env  # if .env.example exists
# OR
nano .env  # create new file
```

2. Edit `.env` with your configuration:
```bash
nano .env
```

Required configuration:
- Zendesk credentials (subdomain, email, API token)
- Wasabi credentials (endpoint, access key, secret key, bucket name)
- Email settings (SMTP server, credentials)
- Scheduler timezone and time

## Running as a System Service (Optional)

To run the application as a system service that starts automatically:

1. Create a systemd service file:
```bash
sudo nano /etc/systemd/system/zendesk-offloader.service
```

2. Add the following content (adjust paths as needed):
```ini
[Unit]
Description=Zendesk to Wasabi Offloader
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/z2w
Environment="PATH=/path/to/z2w/venv/bin"
ExecStart=/path/to/z2w/venv/bin/python /path/to/z2w/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

3. Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable zendesk-offloader
sudo systemctl start zendesk-offloader
```

4. Check service status:
```bash
sudo systemctl status zendesk-offloader
```

5. View logs:
```bash
sudo journalctl -u zendesk-offloader -f
```

## Troubleshooting

### Virtual Environment Issues

If you get errors about the virtual environment:

```bash
# Remove the old venv and recreate
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Permission Issues

If you get permission errors:

```bash
# Make sure scripts are executable
chmod +x setup_debian.sh run_debian.sh
```

### Scheduler Not Working

The scheduler requires `tzlocal` package (included in requirements.txt). If you still have issues:

1. Check logs in `logs/` directory
2. Verify timezone is set correctly in `.env`:
   ```
   SCHEDULER_TIMEZONE=UTC  # or your timezone like America/New_York
   ```
3. Check that the scheduler is starting in the application logs

### Port Already in Use

If port 5000 is already in use, change it in `.env`:
```
ADMIN_PANEL_PORT=8080
```

## Updating

To update the application:

```bash
# Pull latest changes
git pull

# Activate virtual environment
source venv/bin/activate

# Update dependencies
pip install -r requirements.txt --upgrade

# Restart the application
```

If running as a service:
```bash
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart zendesk-offloader
```
