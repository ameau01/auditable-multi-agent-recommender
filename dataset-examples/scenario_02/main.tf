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
  app_name = "app02"
  common_tags = {
    Application = local.app_name
    ManagedBy   = "data-gen-pipeline"
  }
}

resource "aws_launch_template" "compute" {
  name_prefix   = "${local.app_name}-compute-"
  image_id      = "ami-0c55b159cbfafe1f0"   # placeholder Amazon Linux 2
  instance_type = "m5.large"

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, { Tier = "compute" })
  }
}

resource "aws_instance" "compute" {
  count                  = 6
  launch_template {
    id      = aws_launch_template.compute.id
    version = "$Latest"
  }
  tags = merge(local.common_tags, { Tier = "compute" })
}





