#!/bin/bash

# Define color codes
GREEN='\033[0;32m'  # Green for positive messages
RED='\033[0;31m'    # Red for negative messages
NC='\033[0m'        # No Color

# Function to print messages
print_message() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}$1${NC}"
    echo -e "${GREEN}========================================${NC}"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to install a package
install_package() {
    if ! command_exists "$1"; then
        echo -e "${RED}$1 could not be found. Installing $1...${NC}"
        apt-get install -y "$1"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to install $1. Exiting.${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}$1 is already installed.${NC}"
    fi
}

# Check for required commands
echo -e "${GREEN}Checking for required packages...${NC}"
install_package python3
install_package python3-pip
install_package python3-venv

# Check if requirements.txt exists
if [ ! -f requirements.txt ]; then
    echo -e "${RED}requirements.txt not found. Please create it before running this script.${NC}"
    exit 1
fi

# Create and activate virtual environment
if [ ! -d venv ]; then
    print_message "Creating Python virtual environment..."
    python3 -m venv venv
fi

print_message "Activating virtual environment..."
source venv/bin/activate

# Install required Python packages
print_message "Installing required Python packages..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to install Python packages. Exiting.${NC}"
    exit 1
fi

# Copy config example files if real configs don't exist
copy_config() {
    local src="$1"
    local dst="$2"
    if [ ! -f "$dst" ]; then
        if [ -f "$src" ]; then
            print_message "Creating $dst from example..."
            cp "$src" "$dst"
            echo "Please update $dst with your settings."
        else
            echo -e "${RED}Example file $src not found. Skipping.${NC}"
        fi
    else
        echo -e "${GREEN}$dst already exists. Skipping.${NC}"
    fi
}

copy_config config/config.ini.example config/config.ini
copy_config config/bot_config.ini.example config/bot_config.ini
copy_config config/email_config.ini.example config/email_config.ini
copy_config config/db_config.ini.example config/db_config.ini

# Create Logs directory
mkdir -p Logs

print_message "Setup completed successfully!"
echo -e "${GREEN}Next steps:${NC}"
echo "1. Update config/config.ini with your backup configuration."
echo "2. Update config/bot_config.ini with your Telegram bot token (if using notifications)."
echo "3. Update config/email_config.ini with your email settings (if using email alerts)."
echo "4. Activate the venv: source venv/bin/activate"
echo "5. Run the script: python main.py --help"
