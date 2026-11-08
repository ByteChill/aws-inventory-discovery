import * as Primitive from "@radix-ui/react-dropdown-menu"
import { Check } from "lucide-react"
import type * as React from "react"

import { cn } from "@/lib/utils"

const DropdownMenu = Primitive.Root
const DropdownMenuTrigger = Primitive.Trigger
const DropdownMenuRadioGroup = Primitive.RadioGroup

// Menu radius 8 = item radius 4 + padding 4.
function DropdownMenuContent({
  className,
  align = "end",
  sideOffset = 4,
  ...props
}: React.ComponentProps<typeof Primitive.Content>) {
  return (
    <Primitive.Portal>
      <Primitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-36 rounded-lg border bg-popover p-1 text-popover-foreground shadow-float",
          className,
        )}
        {...props}
      />
    </Primitive.Portal>
  )
}

const itemClass =
  "relative flex cursor-default select-none items-center gap-2 rounded-sm py-1.5 pr-3 pl-7 text-sm outline-none data-[highlighted]:bg-subtle-strong data-[disabled]:opacity-50"

function DropdownMenuItem({
  className,
  ...props
}: React.ComponentProps<typeof Primitive.Item>) {
  return <Primitive.Item className={cn(itemClass, className)} {...props} />
}

function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof Primitive.RadioItem>) {
  return (
    <Primitive.RadioItem className={cn(itemClass, className)} {...props}>
      <Primitive.ItemIndicator className="absolute left-2 flex items-center">
        <Check className="size-4" />
      </Primitive.ItemIndicator>
      {children}
    </Primitive.RadioItem>
  )
}

function DropdownMenuCheckboxItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof Primitive.CheckboxItem>) {
  return (
    <Primitive.CheckboxItem className={cn(itemClass, className)} {...props}>
      <Primitive.ItemIndicator className="absolute left-2 flex items-center">
        <Check className="size-4" />
      </Primitive.ItemIndicator>
      {children}
    </Primitive.CheckboxItem>
  )
}

function DropdownMenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof Primitive.Separator>) {
  return (
    <Primitive.Separator
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

export {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
}
