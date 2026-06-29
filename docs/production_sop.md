# Extreme Survival Production SOP

## Goal

Produce one polished 25-58 second Short for roughly $4-$6, optimized for retention, replay, and commentable endings. Default target: 50 seconds.

## Step 1: Pick The Idea

Choose one content bucket:

- Weird Survival Simulations
- Almost Impossible Accidents
- Countdown Survival Tests
- One Rule Survival Worlds
- Impossible Rooms

Write the idea as one sentence:

> You are trapped in [almost impossible situation], and [one physical survival rule].

Reject the idea if:

- the rule needs more than one sentence;
- the threat is invisible;
- the ending depends on lore;
- it feels like a real tragedy;
- it cannot be shown clearly in 9:16.

## Step 2: Score The Idea

Score from 1 to 5.

| Score | Question |
| --- | --- |
| Trap clarity | Can viewers understand the trap in 2 seconds? |
| Rule clarity | Is there one simple physical rule? |
| Visual motion | Does something obvious move or change? |
| Sound potential | Are there strong SFX moments? |
| Twist potential | Is there a clean ending or reversal? |

Only produce ideas scoring 18 or higher out of 25.

## Step 3: Write The Script

Use `docs/prompt_templates.md`.

Script requirements:

- 25-58 seconds, usually 50 seconds.
- Starts in second person.
- Rule appears by 6 seconds.
- Escalation every 5-8 seconds.
- One clever solution attempt.
- One final twist or payoff.

## Step 4: Build The Beat Plan

Create 8-12 beats.

Each beat needs:

- timestamp;
- narration;
- visual action;
- camera view;
- overlay text;
- SFX;
- image prompt;
- video prompt.

Beat duration should usually be 3-6 seconds.

## Step 5: Generate Visuals

Target quality:

- 1080x1920 vertical.
- Clean 3D explainer look.
- Main action centered.
- No generated readable text.
- No gore.

Recommended generation strategy:

- Generate clip 1 as a 15-second Seedance native-audio video.
- Extract the final frame from clip 1.
- Generate clip 2 from that final frame and pass clip 1 as voice/video reference when possible.
- Stitch the two clips, transcribe the native audio, and burn synced captions.
- Do not use still images as final footage.

Budget guide:

- Cheap test: $1-$2.
- Standard Short: $3-$6.
- Premium test: $6-$10 only after a concept proves traction.

## Step 6: Generate Voice

Voice direction:

- clear;
- fast but not rushed;
- direct;
- tense;
- second-person;
- no theatrical horror whisper.

Narration should drive pacing. Cut dead air aggressively.

## Step 7: Add Sound Design

Use `docs/sound_design.md`.

Minimum sound pass:

- first-frame hook sting;
- action-matched SFX;
- timer or pressure texture when relevant;
- riser or silence before twist;
- final impact.

Do not rely on copyrighted trending audio as the core value of the Short.

## Step 8: Add Overlays

Overlay rules:

- 1-4 words.
- Large and phone-readable.
- No full subtitles unless separately testing captions.
- Use text for rules, timers, choices, and twists.

Examples:

- NO EXIT
- 60 SECONDS
- DON'T MOVE
- PICK ONE
- WRONG DOOR
- OXYGEN LOW

## Step 9: Export

Export settings:

- 1080x1920.
- 30 fps minimum.
- H.264 MP4.
- Loud enough for phone speakers without clipping.
- Cover frame selected from the strongest trap image.

## Step 10: Upload Package

For each Short prepare:

- title;
- 1-line description;
- cover frame;
- 3 hashtags maximum;
- internal episode number;
- analytics row in `docs/analytics_log.md`.

Description template:

```text
One situation. One rule. One way out.

#Shorts #Survival #WhatIf
```

## Step 11: Review After 24-72 Hours

Record:

- views;
- viewed vs swiped away;
- average viewed;
- retention drop point;
- comments;
- cost;
- what to repeat;
- what to change.

## Step 12: Compile Winners

After 10-15 Shorts:

- pick the best 7-10;
- create one 8-12 minute compilation;
- add original intro;
- add quick transitions;
- add a stronger ending;
- do not upload raw back-to-back Shorts without packaging.

