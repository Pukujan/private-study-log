# Hermes / Hades OS UX Scope Add-On — Level System and Auto-Onboarding

## Product Direction Update

Hermes / Hades OS should include a level system that gradually unlocks more minions, minion slots, commands, socials, marketplace access, and automation power.

The level system is not just for gamification.

It is the onboarding system.

It controls how much complexity the user sees at each stage.

Core idea:

```txt
Users do not start with the full automation platform.
Users grow into it.
```

---

# Why the Level System Matters

Without levels, Hermes could overwhelm users with:

```txt
too many minions
too many automations
too many social integrations
too many marketplace options
too many commands
too many settings
```

The level system solves this by unlocking features gradually.

It makes Hermes feel:

```txt
guided
gamery
safe
progressive
easy to learn
rewarding
less overwhelming
```

---

# Level System Concept

Users start with a small loadout.

Example Level 1:

```txt
Ask Hermes
1 starter minion
1 basic command
basic inbox
offline chat
```

As users complete simple actions, they unlock more.

Example unlocks:

```txt
send 5 messages
→ unlock second minion slot

create first helper
→ unlock manual automation minion

run 3 helpers successfully
→ unlock marketplace preview

connect first social
→ unlock social minion slot

use commands safely
→ unlock global command slot
```

---

# Level Progression

Example structure:

## Level 1 — New Summoner

```txt
Unlocked:
- Ask Hermes
- Offline chat
- 1 starter minion
- basic profile card
- basic theme
```

Goal:

```txt
Send your first command to Hermes.
```

---

## Level 2 — Helper Tamer

```txt
Unlocked:
- 2 minion slots
- starter minion inventory
- simple reminder helper
- pending command tray
```

Goal:

```txt
Use Hermes to create or run a helper.
```

---

## Level 3 — Tool Keeper

```txt
Unlocked:
- manual automation minion
- tool creator
- basic inbox alerts
- more starter templates
```

Goal:

```txt
Create your first reusable helper.
```

---

## Level 4 — Social Scout

```txt
Unlocked:
- socials page
- locked Discord/Telegram previews
- 1 social minion slot preview
- command examples like !sendcatmeme
```

Goal:

```txt
Explore where Hermes can be connected later.
```

---

## Level 5 — Command Crafter

```txt
Unlocked:
- custom command creator
- 1 global command slot
- command inventory
- command preview cards
```

Goal:

```txt
Create your first callable command.
```

---

## Level 6 — Market Visitor

```txt
Unlocked:
- marketplace browsing
- popular minion previews
- creator cards
- trial/rent/buy UI preview
```

Goal:

```txt
Browse marketplace minions and save one to wishlist.
```

---

## Level 7 — Forge Creator

```txt
Unlocked:
- profile card customization
- showcase minions
- badges
- creator profile preview
- shareable minion shell
```

Goal:

```txt
Customize your profile and showcase your favorite minion.
```

---

## Level 8+ — Automation Adept

```txt
Unlocked:
- more minion slots
- more global command slots
- more social slots
- advanced automations
- marketplace purchases
- creator tools
```

Goal:

```txt
Use Hermes as a full automation companion across tools and socials.
```

---

# Slot Unlocking

The level system should directly control minion capacity.

Example:

```txt
Level 1:
- 1 active minion
- 0 global command slots
- 0 social slots

Level 2:
- 2 active minions
- 0 global command slots
- 0 social slots

Level 4:
- 3 active minions
- 1 social preview slot
- 0 global command slots

Level 5:
- 3 active minions
- 1 global command slot
- 1 social slot

Subscription:
- +1 extra minion slot per social
- +2 global command slots
```

This gives users a reason to keep progressing without immediately forcing payment.

---

# Level Data Shape

```ts
type UserLevelState = {
  id: string
  userId: string
  level: number
  title: string
  xp: number
  nextLevelXp: number
  completedMilestones: string[]
  unlockedFeatures: string[]
  createdAt: string
  updatedAt: string
}
```

---

# Unlock Data Shape

```ts
type UnlockReward = {
  id: string
  levelRequired: number
  key: string
  label: string
  description: string
  rewardType:
    | "minion_slot"
    | "global_command_slot"
    | "social_slot"
    | "theme"
    | "profile_badge"
    | "marketplace_access"
    | "social_preview"
    | "creator_feature"
  quantity: number | null
}
```

---

# Milestone Data Shape

```ts
type LevelMilestone = {
  id: string
  userId: string
  key: string
  label: string
  completed: boolean
  completedAt: string | null
}
```

---

# UX Rules

The level system should be visible but not annoying.

Good UX:

```txt
small progress bar
clear next unlock
celebration animation
simple milestone card
"Next: unlock 1 more minion slot"
```

Bad UX:

```txt
too many stats
complex RPG screens
confusing XP economy
blocking basic usage too aggressively
```

The level system should feel like:

```txt
Hermes is teaching me the app step by step.
```

Not:

```txt
I am grinding points to use basic features.
```

---

# MVP Boundary

MVP should include the level system visually, but not as a fully complex backend economy.

MVP can include:

```txt
level badge
simple progress bar
next unlock preview
locked minion slots
locked social slots
starter level names
basic client-side/demo progression
```

MVP should not include yet:

```txt
full XP backend
complex unlock economy
paid level boosts
creator rank system
public leaderboards
competitive ranking
```

---

# Phase Placement

## MVP / V1

```txt
Visible level system shell
Basic onboarding progress
Level badge
Next unlock preview
Locked minion slots
Starter milestones
```

## V1.5

```txt
Persisted user level state
Real milestone tracking
Saved unlocks
Profile card shows level
Inventory slots respect level
```

## V2

```txt
Social slots unlock by level
Global command slots unlock by level
Marketplace access unlocks gradually
Subscription adds bonus slots
```

## V3

```txt
Full progression system
Creator ranks
Premium unlocks
Advanced badge system
Seasonal cosmetic rewards
Marketplace reputation
```

---

# Final UX Memory

The level system is the auto-onboarding layer.

It gradually unlocks:

```txt
minions
minion slots
global command slots
social slots
marketplace browsing
themes
profile card customization
creator tools
advanced automations
```

This lets Hermes stay simple for new users while still growing into a rich consumer automation platform.
