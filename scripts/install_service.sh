#!/bin/bash
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CURRENT_USER="$(whoami)"

echo -e "${GREEN}Backup Handler - Service Installer${NC}"
echo "Project directory: $PROJECT_DIR"
echo ""

# Detect OS
case "$(uname -s)" in
    Linux*)
        echo -e "${GREEN}Detected Linux - Installing systemd service...${NC}"

        SERVICE_FILE="$PROJECT_DIR/scripts/backup-handler.service"
        DEST="/etc/systemd/system/backup-handler.service"

        if [ ! -f "$SERVICE_FILE" ]; then
            echo -e "${RED}Service file not found: $SERVICE_FILE${NC}"
            exit 1
        fi

        # Substitute placeholders
        sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
            -e "s|__USER__|$CURRENT_USER|g" \
            "$SERVICE_FILE" | sudo tee "$DEST" > /dev/null

        sudo systemctl daemon-reload
        sudo systemctl enable backup-handler.service
        sudo systemctl start backup-handler.service

        echo ""
        echo -e "${GREEN}Service installed and started.${NC}"
        echo "Useful commands:"
        echo "  sudo systemctl status backup-handler"
        echo "  sudo systemctl stop backup-handler"
        echo "  sudo systemctl restart backup-handler"
        echo "  sudo journalctl -u backup-handler -f"
        ;;

    Darwin*)
        echo -e "${GREEN}Detected macOS - Installing launchd service...${NC}"

        PLIST_FILE="$PROJECT_DIR/scripts/com.backup-handler.plist"
        DEST="$HOME/Library/LaunchAgents/com.backup-handler.plist"

        if [ ! -f "$PLIST_FILE" ]; then
            echo -e "${RED}Plist file not found: $PLIST_FILE${NC}"
            exit 1
        fi

        # Substitute placeholders
        sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_FILE" > "$DEST"

        launchctl load "$DEST"

        echo ""
        echo -e "${GREEN}Service installed and loaded.${NC}"
        echo "Useful commands:"
        echo "  launchctl list | grep backup-handler"
        echo "  launchctl unload $DEST"
        echo "  launchctl load $DEST"
        ;;

    *)
        echo -e "${RED}Unsupported OS: $(uname -s)${NC}"
        echo "For Windows, use scripts/install_windows_task.ps1 in PowerShell."
        exit 1
        ;;
esac
