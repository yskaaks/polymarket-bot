#!/bin/bash
set -e

# Configuration
REGION="ap-southeast-2"
INSTANCE_TYPE="t3.micro"
KEY_NAME="polybot-key"
SG_NAME="polybot-sg"

echo "Fetching latest Amazon Linux 2023 AMI for $REGION..."
# Use region argument explicitly so it uses the verified region
AMI_ID=$(aws ssm get-parameters --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64 --query 'Parameters[0].Value' --output text --region $REGION)

echo "Starting EC2 provisioning for Polymarket Bot in $REGION..."

# 1. Create Key Pair
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" > /dev/null 2>&1; then
    echo "Creating key pair: $KEY_NAME..."
    aws ec2 create-key-pair --key-name "$KEY_NAME" --query 'KeyMaterial' --output text > "$KEY_NAME.pem"
    chmod 400 "$KEY_NAME.pem"
    echo "Key pair saved to $KEY_NAME.pem"
else
    echo "Key pair $KEY_NAME already exists or we don't have permission to create it."
fi

# 2. Create Security Group
if ! aws ec2 describe-security-groups --group-names "$SG_NAME" > /dev/null 2>&1; then
    echo "Creating security group: $SG_NAME..."
    VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)
    SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" --description "Polymarket bot security group" --vpc-id "$VPC_ID" --query 'GroupId' --output text)
    
    # Allow SSH access
    aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0
    echo "Created security group $SG_ID and authorized SSH."
else
    SG_ID=$(aws ec2 describe-security-groups --group-names "$SG_NAME" --query "SecurityGroups[0].GroupId" --output text)
    echo "Security group $SG_NAME already exists ($SG_ID)."
fi

# 3. Create User Data script to install packages cleanly
cat << 'EOF' > polybot_user_data.sh
#!/bin/bash
# Install basic dependencies
dnf update -y
dnf install -y git tmux htop gcc python3 python3-devel

# Install uv for the ubuntu/ec2-user
su - ec2-user -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
EOF

# 4. Launch EC2 Instance
echo "Launching EC2 instance ($INSTANCE_TYPE)..."
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --user-data file://polybot_user_data.sh \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=PolymarketBot}]" \
    --query 'Instances[0].InstanceId' \
    --output text)

echo "Instance ID: $INSTANCE_ID"
echo "Waiting for instance to be running..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --query "Reservations[0].Instances[0].PublicIpAddress" --output text)

echo "Instance is running!"
echo "Public IP: $PUBLIC_IP"
echo ""
echo "============================================================"
echo "Next steps to run your bot:"
echo "============================================================"
echo "1. Sync your code to the remote server:"
echo "   rsync -avz --exclude '.git' --exclude '.venv' -e 'ssh -i $KEY_NAME.pem -o StrictHostKeyChecking=no' ./ ec2-user@$PUBLIC_IP:~/polymarket-bot/"
echo ""
echo "2. SSH into the instance:"
echo "   ssh -i $KEY_NAME.pem ec2-user@$PUBLIC_IP"
echo ""
echo "3. Run the bot using tmux so it stays up all day:"
echo "   tmux new -s bot"
echo "   cd ~/polymarket-bot"
echo "   ~/.local/bin/uv run python -m src.strategies.uma_arb_strategy"
echo "   # Press Ctrl+B, then D to detach from tmux and leave it running in the background."
echo "============================================================"

rm polybot_user_data.sh
