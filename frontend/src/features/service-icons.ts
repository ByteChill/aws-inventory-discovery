import {
  Boxes,
  Cloud,
  Container,
  Cpu,
  Globe,
  Inbox,
  KeyRound,
  Megaphone,
  Split,
  Zap,
} from "lucide-react"
import type { ComponentType } from "react"

/** One small icon per service key, used by the menu and the overview's bars. */
export const SERVICE_ICON: Record<
  string,
  ComponentType<{ className?: string }>
> = {
  ec2: Cpu,
  ecs: Container,
  lambda: Zap,
  vpc: Cloud,
  elb: Split,
  route53: Globe,
  sqs: Inbox,
  sns: Megaphone,
  secretsmanager: KeyRound,
  other: Boxes,
}
