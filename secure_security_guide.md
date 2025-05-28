# Oracle Table Comparator - Secure Credential Management

## 🔐 **Security Overview**

**NEVER store passwords directly in configuration files!** This guide shows secure alternatives for managing Oracle database credentials.

## 🎯 **Credential Management Options**

### **Option 1: Environment Variables (Recommended)**

**Setup:**
```bash
# Linux/macOS
export ORACLE_SOURCE_USER="comparison_user"
export ORACLE_SOURCE_PASSWORD="secure_password_123"
export ORACLE_TARGET_USER="comparison_user"
export ORACLE_TARGET_PASSWORD="secure_password_456"

# Windows
set ORACLE_SOURCE_USER=comparison_user
set ORACLE_SOURCE_PASSWORD=secure_password_123
set ORACLE_TARGET_USER=comparison_user
set ORACLE_TARGET_PASSWORD=secure_password_456
```

**Config.yaml:**
```yaml
source_database:
  host: "source-db.company.com"
  port: 1521
  service_name: "PRODDB"
  username: "${ORACLE_SOURCE_USER}"
  password: "${ORACLE_SOURCE_PASSWORD}"
```

**Benefits:**
- ✅ Passwords not stored in files
- ✅ Environment-specific configuration
- ✅ CI/CD pipeline friendly
- ✅ Easy to rotate credentials

---

### **Option 2: External Credential Files**

**Create credential files with restricted permissions:**

**source_db_credentials.json:**
```json
{
  "username": "comparison_user",
  "password": "secure_password_123"
}
```

**Setup file permissions:**
```bash
# Create secure directory
sudo mkdir -p /secure/oracle_credentials
sudo chown comparison_user:comparison_user /secure/oracle_credentials
sudo chmod 750 /secure/oracle_credentials

# Create credential files
echo '{"username":"comparison_user","password":"secure_password_123"}' > /secure/oracle_credentials/source_db.json
echo '{"username":"comparison_user","password":"secure_password_456"}' > /secure/oracle_credentials/target_db.json

# Secure file permissions (read-only for owner)
chmod 600 /secure/oracle_credentials/*.json
```

**Config.yaml:**
```yaml
source_database:
  host: "source-db.company.com"  
  port: 1521
  service_name: "PRODDB"
  credential_file: "/secure/oracle_credentials/source_db.json"

target_database:
  host: "target-db.company.com"
  port: 1521
  service_name: "DRDB"
  credential_file: "/secure/oracle_credentials/target_db.json"
```

**Benefits:**
- ✅ Centralized credential management
- ✅ File-level security controls
- ✅ Easy credential rotation
- ✅ Audit trail for file access

---

### **Option 3: Oracle Wallet (Most Secure)**

**Create Oracle Wallet:**
```bash
# Create wallet directory
mkdir -p /opt/oracle/wallet
cd /opt/oracle/wallet

# Create wallet (requires Oracle client tools)
mkstore -wrl /opt/oracle/wallet -create

# Add database credentials to wallet
mkstore -wrl /opt/oracle/wallet -createCredential source_db comparison_user secure_password_123
mkstore -wrl /opt/oracle/wallet -createCredential target_db comparison_user secure_password_456

# Set wallet password in environment
export WALLET_PASSWORD="wallet_master_password"
```

**Config.yaml:**
```yaml
source_database:
  host: "source-db.company.com"
  port: 1521
  service_name: "PRODDB"
  wallet_location: "/opt/oracle/wallet"
  wallet_password: "${WALLET_PASSWORD}"

target_database:
  host: "target-db.company.com"
  port: 1521
  service_name: "DRDB"
  wallet_location: "/opt/oracle/wallet"
  wallet_password: "${WALLET_PASSWORD}"
```

**Benefits:**
- ✅ Enterprise-grade security
- ✅ Encrypted credential storage
- ✅ Oracle-native solution
- ✅ Centralized wallet management

---

### **Option 4: Interactive Prompts**

**Config.yaml:**
```yaml
source_database:
  host: "source-db.company.com"
  port: 1521
  service_name: "PRODDB"
  prompt_credentials: true

target_database:
  host: "target-db.company.com"
  port: 1521
  service_name: "DRDB"
  prompt_credentials: true
```

**Usage:**
```bash
$ python oracle_comparator_restart.py config.yaml

Username for source-db.company.com: comparison_user
Password for comparison_user@source-db.company.com: [hidden input]
Username for target-db.company.com: comparison_user  
Password for comparison_user@target-db.company.com: [hidden input]

🚀 Starting Oracle comparison...
```

**Benefits:**
- ✅ Maximum security (no stored passwords)
- ✅ Good for ad-hoc comparisons
- ✅ Simple setup
- ❌ Not suitable for automation

---

## 🛡️ **Security Best Practices**

### **1. File Permissions**
```bash
# Configuration files
chmod 640 config.yaml                    # Read-write owner, read group
chgrp oracle_users config.yaml

# Credential files  
chmod 600 /secure/credentials/*.json     # Read-write owner only
chown comparison_user:comparison_user /secure/credentials/*.json

# Wallet files
chmod 600 /opt/oracle/wallet/*          # Wallet files owner-only
chown oracle:oracle /opt/oracle/wallet/
```

### **2. Environment Variable Security**
```bash
# Use shell profiles for persistent environment variables
echo 'export ORACLE_SOURCE_USER="comparison_user"' >> ~/.bashrc
echo 'export ORACLE_SOURCE_PASSWORD="$(cat /secure/passwords/source.txt)"' >> ~/.bashrc

# Or use systemd environment files for services
# /etc/systemd/system/oracle-comparison.service.d/environment.conf
[Service]
EnvironmentFile=/secure/environment/oracle.env
```

### **3. Credential Rotation**
```bash
#!/bin/bash
# rotate_oracle_credentials.sh

# Update credential files
echo '{"username":"comparison_user","password":"NEW_PASSWORD_123"}' > /secure/oracle_credentials/source_db.json

# Update environment variables
export ORACLE_SOURCE_PASSWORD="NEW_PASSWORD_123"

# Restart any running comparisons
systemctl restart oracle-comparison
```

### **4. Audit and Monitoring**
```bash
# Monitor credential file access
auditctl -w /secure/oracle_credentials/ -p r -k oracle_cred_access

# Log environment variable access (if needed)
alias env='env | grep -v PASSWORD'
```

---

## 🚀 **Production Deployment Examples**

### **Docker Container**
```dockerfile
FROM python:3.9-slim

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application
COPY oracle_comparator_restart.py .
COPY config.yaml .

# Create non-root user
RUN useradd -m -u 1000 comparison_user
USER comparison_user

# Use environment variables for credentials
CMD ["python", "oracle_comparator_restart.py", "config.yaml"]
```

**Docker run with secrets:**
```bash
docker run -d \
  -e ORACLE_SOURCE_USER="comparison_user" \
  -e ORACLE_SOURCE_PASSWORD="$(cat /secure/source_password.txt)" \
  -e ORACLE_TARGET_USER="comparison_user" \
  -e ORACLE_TARGET_PASSWORD="$(cat /secure/target_password.txt)" \
  -v /secure/config:/app/config:ro \
  oracle-comparator
```

### **Kubernetes Deployment**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: oracle-credentials
type: Opaque
data:
  source-username: Y29tcGFyaXNvbl91c2Vy  # base64 encoded
  source-password: c2VjdXJlX3Bhc3N3b3Jk  # base64 encoded
  target-username: Y29tcGFyaXNvbl91c2Vy
  target-password: c2VjdXJlX3Bhc3N3b3Jk
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: oracle-comparator
spec:
  template:
    spec:
      containers:
      - name: comparator
        image: oracle-comparator:latest
        env:
        - name: ORACLE_SOURCE_USER
          valueFrom:
            secretKeyRef:
              name: oracle-credentials
              key: source-username
        - name: ORACLE_SOURCE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: oracle-credentials
              key: source-password
```

### **CI/CD Pipeline (GitHub Actions)**
```yaml
name: Oracle Table Comparison
on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM

jobs:
  compare-tables:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    
    - name: Setup Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: pip install -r requirements.txt
    
    - name: Run comparison
      env:
        ORACLE_SOURCE_USER: ${{ secrets.ORACLE_SOURCE_USER }}
        ORACLE_SOURCE_PASSWORD: ${{ secrets.ORACLE_SOURCE_PASSWORD }}
        ORACLE_TARGET_USER: ${{ secrets.ORACLE_TARGET_USER }}
        ORACLE_TARGET_PASSWORD: ${{ secrets.ORACLE_TARGET_PASSWORD }}
      run: python oracle_comparator_restart.py config.yaml --no-progress
```

---

## ⚠️ **Security Warnings**

### **DON'T DO THIS:**
```yaml
# ❌ NEVER store passwords in plain text
source_database:
  password: "my_secret_password"

# ❌ NEVER commit credential files to version control
git add credentials.json

# ❌ NEVER use weak passwords
password: "123456"

# ❌ NEVER use the same password for all environments
prod_password: "same_password"
dev_password: "same_password"
```

### **DO THIS INSTEAD:**
```yaml
# ✅ Use environment variables
source_database:
  password: "${ORACLE_SOURCE_PASSWORD}"

# ✅ Use strong, unique passwords
# Generated with: openssl rand -base64 32

# ✅ Use different credentials per environment
# prod_user / staging_user / dev_user

# ✅ Rotate passwords regularly
# Implement automated rotation every 90 days
```

This comprehensive security approach ensures your Oracle credentials are protected while maintaining operational flexibility for the table comparison tool.
