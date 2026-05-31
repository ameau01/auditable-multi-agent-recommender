terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

locals {
  app_name = "app08"
  common_tags = {
    Application = local.app_name
    ManagedBy   = "data-gen-pipeline"
  }
}

resource "aws_launch_template" "compute" {
  name_prefix   = "${local.app_name}-compute-"
  image_id      = "ami-0c55b159cbfafe1f0"
  instance_type = "m5.large"

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, { Tier = "compute" })
  }
}

resource "aws_autoscaling_group" "compute" {
  name                = "${local.app_name}-compute-asg"
  min_size            = 8
  max_size            = 12
  desired_capacity    = 8
  vpc_zone_identifier = ["subnet-placeholder-a", "subnet-placeholder-b"]

  launch_template {
    id      = aws_launch_template.compute.id
    version = "$Latest"
  }

  tag {
    key                 = "Application"
    value               = local.app_name
    propagate_at_launch = true
  }
  tag {
    key                 = "Tier"
    value               = "compute"
    propagate_at_launch = true
  }
}

resource "aws_db_instance" "database_primary" {
  identifier              = "${local.app_name}-db-primary"
  engine                  = "postgres"
  engine_version          = "16.1"
  instance_class          = "db.r6g.xlarge"
  allocated_storage       = 500
  storage_encrypted       = true
  skip_final_snapshot     = true
  username                = "appuser"
  password                = "placeholder-rotate-in-prod"
  publicly_accessible     = false

  tags = merge(local.common_tags, { Tier = "database", Role = "primary" })
}




resource "aws_security_group_rule" "compute_to_database" {
  description       = "app08: compute tier queries database tier"
  type              = "ingress"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  security_group_id = "sg-placeholder-database"
  source_security_group_id = "sg-placeholder-compute"
}
