# Hades / Hermes UX Pattern — Natural-Language Bot Creator Chat

## Purpose

This document defines the UX pattern for creating Hades/Hermes minions through chat.

The goal is to let users create bots/minions naturally, without forcing them through a rigid form or technical wizard.

Core idea:

```txt
User describes what they want.
Hermes infers the minion schema.
Hermes asks only for missing or ambiguous fields.
Hermes shows a live draft card.
User can test, edit, save, and assign the minion.
```

This should feel like:

```txt
talking to a helpful assistant
building a small helper together
watching the minion take shape
```

Not like:

```txt
filling out a complicated automation form
configuring technical JSON
writing bot code
managing backend workers
```

---

# Core UX Rule

Hermes should support both creation styles:

```txt
1. Guided mode
2. Direct natural-language mode
```

## Guided Mode

Hermes asks the user step by step.

Example:

```txt
User: I want to make a bot that sends cat memes.

Hermes: Where should it work?

User: Discord.

Hermes: What command should trigger it?

User: !sendcatmeme.

Hermes: What should it do?

User: Send a random cat meme gif.

Hermes: Here is the minion draft. Want to test it?
```

## Direct Natural-Language Mode

The user describes the whole thing in one sentence.

Example:

```txt
User: I want a command to send cat memes in Discord.
```

Hermes should infer as much as possible:

```json
{
  "name": "Cat Meme Minion",
  "category": "fun",
  "targetSocial": "discord",
  "triggerType": "command",
  "commandName": null,
  "action": "send a random cat meme",
  "status": "draft"
}
```

Then Hermes should ask only for the missing field:

```txt
Hermes: Nice — I can make that. What command should trigger it?

Suggestions:
!catmeme
!sendcatmeme
!cat
```

---

# Experience Principle

Hermes should behave like this:

```txt
Fill what you can.
Ask only what you need.
Suggest sensible defaults.
Show the draft as it updates.
Let the user test before saving.
```

Hermes should not behave like this:

```txt
Ask every question one by one even when the user already gave enough information.
Force the user to fill a form.
Expose raw JSON to normal users.
Require technical configuration language.
```

---

# Minion Draft Schema

Hermes should maintain a draft object behind the chat.

```ts
type MinionDraft = {
  name: string | null
  description: string | null
  category:
    | "task"
    | "chat"
    | "shopping"
    | "social"
    | "dev"
    | "fun"
    | "meeting"
    | "personal"
    | null
  targetSocial:
    | "discord"
    | "telegram"
    | "email"
    | "github"
    | "private"
    | null
  triggerType:
    | "manual"
    | "command"
    | "watcher"
    | "scheduled"
    | "social_event"
    | null
  commandName: string | null
  action: string | null
  responseStyle:
    | "funny"
    | "helpful"
    | "short"
    | "detailed"
    | null
  safetyMode: "ask_first" | "auto" | "draft_only"
  testInput: string | null
  status: "incomplete" | "ready_to_test" | "tested" | "saved"
}
```

The user should not need to see this raw schema.

The UI should show a friendly draft card instead.

---

# Required Fields for MVP

Keep MVP minion creation simple.

Required fields:

```txt
name
targetSocial or private
triggerType
action
```

If `triggerType = command`, also require:

```txt
commandName
```

Everything else can use defaults.

Default values:

```txt
safetyMode: ask_first
responseStyle: helpful
status: incomplete
```

---

# Live Draft Card

While the user chats, the UI should show a live minion draft card.

On desktop/tablet:

```txt
Chat on the left or center
Draft card on the right
```

On mobile:

```txt
Chat first
Draft card collapses below chat
Missing fields appear as chips
```

Draft card example:

```txt
Cat Meme Minion

Works in:
Discord

Command:
!sendcatmeme

Action:
Send a random cat meme GIF

Mode:
Ask-first until connected

Status:
Ready to test

[Test] [Save] [Assign]
```

---

# Draft Card States

## 1. Draft Incomplete

Used when required fields are missing.

Example:

```txt
Missing:
- command name
```

UI actions:

```txt
suggest command names
ask next natural question
allow user to type answer
```

---

## 2. Ready to Test

Used when all required fields are filled.

UI actions:

```txt
Test
Edit
Save Draft
```

---

## 3. Test Running

Used while Hermes simulates the minion.

UI copy:

```txt
Testing your minion...
```

---

## 4. Test Passed

Used when the test output is successful.

Example:

```txt
Test result:
🐱 random cat meme sent
```

UI actions:

```txt
Save Minion
Assign to Social
Edit
Test Again
```

---

## 5. Saved

Used once the minion is saved to inventory.

UI actions:

```txt
View in Inventory
Assign to Social
Create Another
```

---

# Example Flows

## Example 1 — Fully Specified Request

User:

```txt
Make me a Discord command called !sendcatmeme that sends a random cat meme gif.
```

Hermes:

```txt
Done — I drafted this minion:

Cat Meme Minion
Works in: Discord
Command: !sendcatmeme
Action: Send a random cat meme GIF
Mode: Ask-first until connected

Want to test it?
```

Hermes should not ask unnecessary questions.

---

## Example 2 — Missing Command

User:

```txt
I want a command to send cat memes in Discord.
```

Hermes:

```txt
I can make that. What should the command be?

Suggested:
!catmeme
!sendcatmeme
!catgif
```

---

## Example 3 — Missing Social

User:

```txt
Make a bot that summarizes long chats.
```

Hermes:

```txt
Good idea. Where should this minion work first?

Options:
Discord
Telegram
Email
Private in Hades
```

---

## Example 4 — Ambiguous Action

User:

```txt
Make a deal bot.
```

Hermes:

```txt
Do you want this minion to:

1. Watch a specific product price
2. Search for promo codes
3. Alert you when something goes on sale
```

---

## Example 5 — Private Minion

User:

```txt
Make a private helper that turns my messy notes into task cards.
```

Hermes should infer:

```txt
targetSocial: private
category: task
triggerType: manual
action: turn messy notes into task cards
```

Hermes:

```txt
Great — I drafted a private Task Helper minion.

It will turn messy notes into clean task cards inside Hades.

Want to test it with a sample note?
```

---

# Question Style

Hermes should ask natural, contextual questions.

Bad:

```txt
Please provide targetSocial.
Please provide triggerType.
Please provide commandName.
```

Good:

```txt
Where should this minion work first?
What should people type to call it?
Should it reply automatically or ask you first?
Do you want this private in Hades or available in Discord?
```

---

# Suggestion Chips

Hermes should use quick suggestion chips whenever possible.

Examples:

```txt
Discord
Telegram
Email
Private in Hades
```

```txt
!catmeme
!sendcatmeme
!catgif
```

```txt
Ask first
Auto reply
Draft only
```

```txt
Test it
Save it
Edit command
Assign to Discord
```

---

# Testing Pattern

Testing should feel safe and playful.

The user should be able to test before saving.

Example:

```txt
Hermes: Want to test it with a sample command?

User: yes

Hermes: Simulating:
User types: !sendcatmeme

Output:
🐱 random cat meme sent
```

For MVP, tests can be simulated inside Hades.

MVP does not need to call real Discord, Telegram, or external services.

---

# Save and Assign Pattern

After testing, Hermes should guide the user into saving and assignment.

Example:

```txt
Hermes: Test passed. Want to save this minion?

User: save it

Hermes: Saved. Should I assign it to Discord now?

User: yes
```

Then the assignment card should show:

```txt
Discord
Assigned Minions:
- Cat Meme Minion
Command:
!sendcatmeme
Status:
Draft / Not connected
```

If the social is not truly connected yet, Hermes should be honest:

```txt
Saved and assigned as a preview. Discord connection is not live yet.
```

---

# Offline Behavior

Minion creation should support the same offline-safe behavior as chat.

If the user sends creation messages offline:

```txt
message appears pending
draft state saves locally
pending messages sync in order
Hermes continues after sync
```

Pending local creation messages may be edited or undone before sync.

Once synced, corrections should be sent as new messages.

---

# MVP Boundary

MVP should implement:

```txt
natural-language minion creation
schema filling
missing field questions
suggestion chips
live draft card
simulated test
save minion
assign to social placeholder
offline-safe chat messages
```

MVP should not implement:

```txt
real Discord bot deployment
real Telegram bot deployment
real marketplace publishing
payments
credits
creator revenue
external API execution for fun commands
complex version history
post-sync message editing
```

---

# Final UX Memory

Hermes bot creation should be:

```txt
natural-language-first
schema-backed
guided only when needed
draft-card visible
test-before-save
assign-after-save
offline-safe
```

The user should be able to say:

```txt
I want a command to send cat memes in Discord.
```

And Hermes should turn that into a minion draft by inferring what it can, asking for only what is missing, then helping the user test and save it.
