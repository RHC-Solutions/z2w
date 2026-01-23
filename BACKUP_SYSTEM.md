# Automated Backup System

## Overview
The system now automatically creates full backups of the entire application daily at 00:00 UTC and sends them to Telegram and Slack.

## Features

### 1. Automated Daily Backups
- **Schedule**: Every day at 00:00 UTC (midnight)
- **Contents**: Complete application backup including:
  - All Python code and modules
  - Database (tickets.db with all processed tickets)
  - Configuration files (.env, config.py)
  - Logs and archives
  - Static files and templates
- **Size**: ~31-32 MB compressed (tar.gz)
- **Retention**: Keeps last 7 days of backups
- **Location**: `/opt/z2w/backups/`

### 2. Delivery Methods

#### Telegram
- ✅ **Working**: Backup file automatically sent to Telegram
- Uses `sendDocument` API to upload files
- Includes detailed message with backup info
- File size, timestamp, and contents listed

#### Slack
- ⚠️ **Requires Setup**: Optional Slack file upload
- Notification sent via webhook (always works)
- File upload requires `SLACK_BOT_TOKEN` environment variable
- To enable file uploads:
  1. Create a Slack App at https://api.slack.com/apps
  2. Add 'files:write' OAuth scope
  3. Install app to workspace
  4. Get Bot User OAuth Token
  5. Set `SLACK_BOT_TOKEN` in `.env` file

### 3. Manual Backup
- Available via Admin Dashboard
- Click "Backup Now" button
- Creates immediate backup and sends to Telegram/Slack
- Useful for pre-deployment backups or testing

## Technical Details

### New Files
- **backup_manager.py**: Core backup functionality
  - Creates tar.gz archives
  - Manages backup rotation (keeps 7 days)
  - Verifies backup integrity
  - Excludes unnecessary files (__pycache__, .git, venv)

### Modified Files
- **scheduler.py**: Added `backup_job()` and scheduling
- **telegram_reporter.py**: Added `send_file()` method
- **slack_reporter.py**: Added `send_file()` method with bot token support
- **admin_panel.py**: Added `/api/backup_now` endpoint
- **templates/dashboard.html**: Added "Backup Now" button

### Scheduled Jobs
The system now runs three daily jobs:
1. **00:00 UTC** - Daily Backup (new)
2. **05:00 UTC** - Daily Zendesk Offload (existing)
3. **01:00 UTC** - Daily Log Archiving (existing)

## Usage

### View Scheduled Jobs
```bash
tail -50 /opt/z2w/logs/app.log | grep -E "(Scheduled|Job|Next run)"
```

### Manual Backup via CLI
```bash
cd /opt/z2w
.venv/bin/python3 -c "from scheduler import OffloadScheduler; sched = OffloadScheduler(); sched.run_backup_now()"
```

### Manual Backup via Dashboard
1. Login to admin panel (http://your-server:5000)
2. Go to Dashboard
3. Click "Backup Now" button
4. Wait for backup to complete
5. Check Telegram for backup file

### Check Backup Files
```bash
ls -lh /opt/z2w/backups/
```

### Verify Backup Integrity
```bash
tar -tzf /opt/z2w/backups/z2w_backup_YYYYMMDD_HHMMSS.tar.gz | head -20
```

### Restore from Backup
```bash
# Extract backup to temporary location
tar -xzf /opt/z2w/backups/z2w_backup_YYYYMMDD_HHMMSS.tar.gz -C /tmp/

# Stop service
systemctl stop z2w.service

# Backup current state (optional)
mv /opt/z2w /opt/z2w.old

# Restore
mv /tmp/z2w /opt/

# Restart service
systemctl start z2w.service
```

## Testing

### Test Results (2026-01-23)
- ✅ Backup created: 31.31 MB
- ✅ File sent to Telegram successfully
- ✅ Contains 5,854 files
- ✅ Includes database, code, configs, logs
- ✅ Backup integrity verified
- ✅ Scheduled for daily execution at 00:00 UTC
- ⚠️ Slack file upload requires SLACK_BOT_TOKEN (notification sent via webhook)

### Manual Test
```bash
cd /opt/z2w
.venv/bin/python3 -c "
from scheduler import OffloadScheduler
import logging
logging.basicConfig(level=logging.INFO)
sched = OffloadScheduler()
sched.run_backup_now()
"
```

## Monitoring

Check backup logs:
```bash
tail -f /opt/z2w/logs/app.log | grep -i backup
```

View backup status in Telegram messages

## Git History
- Commit 2953d76: Feature: Automated daily backup system
- Commit 3872a48: Add backups directory and backup files to gitignore

## Next Steps (Optional)

1. **Enable Slack File Upload**: Set `SLACK_BOT_TOKEN` in `.env` to enable file uploads to Slack
2. **Adjust Retention**: Modify `max_backups` in `backup_manager.py` (default: 7 days)
3. **Change Schedule**: Modify backup time in `scheduler.py` (default: 00:00 UTC)
4. **Off-site Backup**: Consider syncing backups to another server or cloud storage

## Security Notes
- Backup files contain sensitive data (database, credentials in .env)
- Files are sent via secure APIs (Telegram, Slack)
- Keep Telegram and Slack access restricted
- Backup files in `/opt/z2w/backups/` should have restricted permissions
- Consider encrypting backups for additional security

