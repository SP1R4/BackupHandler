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

# Check if requirements.txt exists
if [ ! -f requirements.txt ]; then
    echo -e "${RED}requirements.txt not found. Please create it before running this script.${NC}"
    exit 1
fi

# Install required Python packages
print_message "Installing required Python packages..."
pip3 install -r requirements.txt
if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to install Python packages. Exiting.${NC}"
    exit 1
fi

# Create .env file from sample if it doesn't exist
if [ ! -f .env ]; then
    print_message "Creating .env file from sample..."
    cp .env.sample .env
    echo "Please update the .env file with your environment variables."
else
    print_message ".env file already exists. Skipping creation."
fi

# Create config/config.ini file from sample if it doesn't exist
if [ ! -f config/config.ini ]; then
    print_message "Creating config/config.ini file from sample..."
    cp config/config.sample.ini config/config.ini
    echo "Please update the config/config.ini file with your configuration settings."
else
    print_message "config/config.ini file already exists. Skipping creation."
fi

print_message "Setup completed successfully!"
echo -e "${GREEN}Next steps:${NC}"
echo "1. Update the .env file with your environment variables."
echo "2. Update the config/config.ini file with your configuration settings."
echo "3. Run the script using: python3 main.py --config config/config.ini"