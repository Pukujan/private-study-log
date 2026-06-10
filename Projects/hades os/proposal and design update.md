# Hades / Hermes OS Scope Add-On — Minion Platform, Marketplace, Inventory, and Social Bots

## Product Direction Update

Hades/Hermes OS should feel like a **consumer-friendly automation companion**, not an enterprise automation dashboard.

The user should not feel like they are configuring technical workflows.

They should feel like they are collecting, activating, and guiding helpful little automation companions.

Use the term:

```txt
Minions
```

instead of:

```txt
bots
agents
workers
automation units
```

Minions are small reusable helpers that can:

```txt
watch prices
find discounts
summarize chats
send memes
draft replies
watch social channels
summarize Discord or Telegram conversations
create GitHub task packets
send alerts
join supported meeting/chat contexts later
trigger commands like !sendcatmeme
```

The product should feel:

```txt
gamery
smooth
guided
friendly
mobile-first
consumer-readable
not overwhelming
```

---

# Core UX Rule

Do not overwhelm the user with too many active minions.

The platform should come with some prebuilt starter minions, but most should be locked, inactive, or available through the marketplace.

The user should always understand:

```txt
What minions do I have?
Where are they active?
What socials can call them?
How many slots do I have left?
Which ones are global?
Which ones are connected to Discord, Telegram, email, GitHub, etc.?
```

---

# Minion Concept

A Minion is a reusable automation helper.

```ts
type Minion = {
  id: string
  name: string
  description: string
  category:
    | "social"
    | "shopping"
    | "productivity"
    | "content"
    | "dev"
    | "fun"
    | "meeting"
    | "personal"
  triggerType: "manual" | "command" | "watcher" | "scheduled" | "social_event"
  commandName: string | null
  status: "owned" | "active" | "inactive" | "locked" | "trial" | "rented"
  visibility: "private" | "shared" | "marketplace"
  createdBy: "system" | "user" | "creator"
  creatorId: string | null
  priceCredits: number | null
  rentalPriceCredits: number | null
  subscriptionRequired: boolean
}
```

Examples:

```txt
Price Watcher Minion
Discount Finder Minion
Chat Summarizer Minion
Discord Recap Minion
Telegram Companion Minion
Cat Meme Minion
GitHub Task Packet Minion
Meeting Notes Minion
Job Lead Watcher Minion
Draft Reply Minion
```

---

# Starter Minions

MVP or early V1 should include a small number of visible starter minions.

Do not activate too many by default.

Suggested starter set:

```txt
1. Ask Hermes
2. Manual Task Helper
3. GitHub Task Packet Helper
4. Simple Reminder Helper
5. Draft Reply Helper
```

Locked / future starter examples:

```txt
Price Watcher
Discount Finder
Discord Summarizer
Telegram Companion
Cat Meme Command
Meeting Notes
Daily Inbox Digest
```

The UI may show these as locked or coming soon, but should not implement all of them in MVP.

---

# Social Minions

Socials should be first-class in the product.

Supported future social surfaces:

```txt
Discord
Telegram
Email
GitHub
Slack later
WhatsApp later if technically possible
Browser extension later
```

Each connected social should show:

```txt
connected status
active minions
available commands
global commands allowed here
social-specific minions
slot usage
permissions
safe mode / ask-before-send setting
```

Example commands:

```txt
!sendcatmeme
!summarize
!watchprice
!remindme
!draftreply
!ticket
!dealwatch
```

Important rule:

```txt
A minion can be globally callable or social-specific.
```

Example:

```txt
!sendcatmeme
= global command, usable in Discord and Telegram if enabled

Discord Summary Minion
= social-specific, only active in selected Discord servers/channels
```

---

# Inventory System

Hades/Hermes should have an inventory system so users cannot activate too many minions at once.

This keeps the product simple, prevents automation overload, and creates a natural monetization model.

Inventory concepts:

```txt
Owned Minions
Active Minions
Inactive Minions
Global Command Slots
Social-Specific Slots
Trial Minions
Rented Minions
Featured Minions
```

Example slot limits:

```txt
Free plan:
- 3 owned active minions
- 1 global command slot
- 1 minion per connected social

Subscription:
- +2 global command slots
- +1 extra minion slot per social
- extra marketplace discounts
```

Data shape:

```ts
type MinionInventory = {
  id: string
  userId: string
  minionId: string
  ownershipType: "free" | "purchased" | "rented" | "trial" | "subscription"
  status: "active" | "inactive" | "expired"
  assignedScope: "global" | "social" | "private"
  assignedSocialId: string | null
  activatedAt: string | null
  expiresAt: string | null
}
```

Slot shape:

```ts
type MinionSlot = {
  id: string
  userId: string
  scope: "global" | "social"
  socialId: string | null
  maxSlots: number
  usedSlots: number
}
```

---

# Marketplace

The marketplace lets users browse and acquire working minions.

Marketplace goals:

```txt
discover popular minions
browse by category
browse by creator/developer
buy minions
rent minions before buying
try minions with limited trial
share your own minions
sell or distribute your minions
```

Marketplace categories:

```txt
Popular
Featured
Shopping
Social
Productivity
Content
Developer Tools
Fun
Meetings
Personal Assistant
Creator Picks
New This Week
```

Marketplace minion shape:

```ts
type MarketplaceMinion = {
  id: string
  minionId: string
  title: string
  subtitle: string
  description: string
  category: string
  creatorId: string
  rating: number | null
  installs: number
  priceCredits: number | null
  rentalPriceCredits: number | null
  trialAvailable: boolean
  featured: boolean
  status: "listed" | "delisted" | "under_review"
  createdAt: string
  updatedAt: string
}
```

---

# Creator System

Users should eventually be able to publish and share their minions.

Creator features:

```txt
creator profile
creator storefront
creator reputation
popular creator page
creator revenue tracking
published minions
private share link
public marketplace listing
```

Users should be able to browse:

```txt
popular creators
favorite creators
featured developers
creator collections
```

Example:

```txt
Browse minions by your favorite developer.
Rent their automation before buying.
Subscribe to get extra slots and creator discounts.
```

---

# Credit System

Hades/Hermes can use a credit system for marketplace transactions.

Credits can be used to:

```txt
buy minions
rent minions
try premium automations
pay creators
unlock featured automation packs
exchange/sell marketplace minions if allowed
```

Credit shape:

```ts
type CreditWallet = {
  id: string
  userId: string
  balance: number
  updatedAt: string
}
```

Transaction shape:

```ts
type CreditTransaction = {
  id: string
  userId: string
  amount: number
  type:
    | "purchase_minion"
    | "rent_minion"
    | "creator_sale"
    | "refund"
    | "subscription_bonus"
    | "marketplace_exchange"
  relatedMinionId: string | null
  createdAt: string
}
```

---

# Paywall and Subscription

Monetization should be natural, not aggressive.

Possible paid features:

```txt
extra minion slots
extra global command slots
premium minions
featured automation packs
creator minions
rent-before-buy
higher usage limits
advanced socials
longer history
more alert destinations
```

Subscription example:

```txt
Hermes Plus:
- +1 extra minion slot per connected social
- +2 global command slots
- monthly credits
- access to featured minion trials
- better marketplace discovery
```

Paywall should appear when the user tries to:

```txt
activate more minions than their inventory allows
use premium minion
rent/buy marketplace minion
enable extra global commands
connect advanced social automation
```

---

# UX Direction

The interface should feel like:

```txt
collecting helpful companions
equipping minions into slots
activating simple powers
connecting them to places you already chat
getting useful alerts back
```

Not like:

```txt
configuring enterprise automations
managing microservices
deploying workers
editing YAML
building agent infrastructure
```

Preferred UI language:

```txt
Minions
Inventory
Slots
Equip
Activate
Summon
Marketplace
Creator
Trial
Rent
Buy
Global Command
Social Slot
Inbox
```

Use sparingly and clearly. Keep it understandable for normal users.

---

# Updated Main Tabs

Suggested consumer tabs:

```txt
Home
Minions
Socials
Inbox
Market
Me
```

Alternative shorter mobile nav:

```txt
Home
Minions
Socials
Inbox
Me
```

Marketplace can be reachable from Minions or Me if five tabs are too many.

---

# Updated MVP Boundary

This minion platform should influence the UI direction now, but most marketplace/paywall behavior should not be implemented in MVP.

MVP should implement or show:

```txt
mobile-first Home
Ask Hermes chat
offline pending messages
starter minion cards
manual minion/tool creation
active minion inventory preview
locked Socials cards
locked Marketplace preview
Inbox preview
GitHub task packet helper as one advanced minion
```

MVP should not implement yet:

```txt
real marketplace payments
real creator payouts
real credit economy
real bot rental
real bot resale/exchange
real Discord bot integration
real Telegram bot integration
real meeting transcription
real price scraping
full paywall logic
multi-tenant creator storefronts
```

These can be visible as locked future systems.

---

# Phase Placement

## MVP / V1

```txt
Mobile-first Hermes shell
Ask Hermes chat
Offline pending messages
Starter minion cards
Manual tools/automations as minions
Inventory preview
Locked marketplace/social previews
GitHub task packet minion
```

## V1.5

```txt
Better minion templates
Minion categories
Simple private minion sharing
Improved inventory rules
Basic social connection prep
```

## V2

```txt
Real social integrations
Discord/Telegram command minions
Notification inbox
Price/deal watcher prototypes
Creator sharing beta
Slot limits
Basic subscription/paywall model
```

## V3

```txt
Marketplace
Creator economy
Credits
Rent-before-buy
Featured minions
Popular creator pages
Bot exchange/resale if allowed
Advanced automation packs
Global/social minion inventory system
```

---

# Design Theme Reference

Use the forge/fire/gamery identity from the previous Hades OS landing page:

```txt
ember particles
warm orange/gold glow
dark forge background
animated energy core
rounded panels
theme changer
premium fantasy-tech feel
```

But adapt it to a mobile-first consumer app.

The theme should become:

```txt
Hermes companion app
gamery minion inventory
smooth mobile command hub
friendly automation marketplace
```

Not:

```txt
enterprise agent control plane
developer ops console
terminal-heavy dashboard
```

---

# Final Product Memory

The product direction is now:

```txt
Hermes/Hades OS is a mobile-first consumer automation companion.

Users talk to Hermes, collect minions, equip them into social/global slots, receive alerts in an inbox, and later discover or buy working minions from a marketplace.

MVP stays scoped:
offline chat, starter minions, manual tools, manual automations, GitHub helper, locked socials, locked marketplace, and mobile-first guided UX.

Later phases add socials, inventory limits, subscriptions, credits, creator marketplace, rent-before-buy, and shareable minions.
```
