set -e

CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "✅ Installing uv (Python package manager)"
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
else
    echo "✅ uv is already installed. Updating to latest version."
    uv self update
fi

echo "✅ Installing project dependencies with uv"
uv sync

service_name=$(uv run config --project-name)
service_port=$(uv run config --flask-port)

echo "📋 Configuration:"
{
    uv run config --all | while IFS='=' read -r key value; do
        echo -e "   ${CYAN}${key}${NC}|${YELLOW}${value}${NC}"
    done
    echo -e "   ${CYAN}cloudflare_domain${NC}|${YELLOW}${service_name}.mnalavadi.org${NC}"
} | column -t -s '|'

echo "✅ Copying service file to systemd directory"
sudo cp install/projects_${service_name}.service /lib/systemd/system/projects_${service_name}.service

echo "✅ Setting permissions for the service file"
sudo chmod 644 /lib/systemd/system/projects_${service_name}.service

echo "✅ Reloading systemd daemon"
sudo systemctl daemon-reload
sudo systemctl daemon-reexec

echo "✅ Enabling the service: projects_${service_name}.service"
sudo systemctl enable projects_${service_name}.service
sudo systemctl restart projects_${service_name}.service
sudo systemctl status projects_${service_name}.service --no-pager

echo "✅ Adding Cloudflared service"
/home/mnalavadi/add_cloudflared_service.sh ${service_name}.mnalavadi.org $service_port
echo "✅ Configuring Cloudflared DNS route"
cloudflared tunnel route dns raspberrypi-tunnel ${service_name}.mnalavadi.org
echo "✅ Restarting Cloudflared service"
sudo systemctl restart cloudflared

echo "✅ Setup completed successfully! 🎉"
