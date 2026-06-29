# Exit Scenario Prompt Templates

## Script Prompt

Use this to generate one Short script.

```text
Write a 25-58 second YouTube Shorts script for Exit Scenario. Default target: 50 seconds.

Channel concept:
Fictional almost-impossible survival situations told in second person. Fast, simple, visual, and sound-driven. Semi-realistic viral 3D simulation explainer style, not slow context-heavy storytelling and not escape-room lore.

Topic:
[PASTE TOPIC]

Rules:
- Start with "You are..." / "Your..." / "What happens if..." / another direct second-person hook.
- Present one almost-impossible physical survival problem in the first sentence.
- Introduce exactly one clear physical survival rule by 6 seconds.
- Include escalation every 5-8 seconds.
- Include one clever survival move.
- End with a payoff or twist.
- No historical or research context.
- No real tragedy.
- No gore.
- No chapters.
- No complex lore.
- Use short sentences.
- Write narration only, no markdown.

Return:
1. Title
2. Narration script
3. Beat list with timestamps
4. On-screen text moments, 1-4 words each
5. Required SFX moments
```

## Beat Plan Prompt

```text
Turn this Exit Scenario script into a 9:16 production beat plan for a tricky survival situation.

Script:
[PASTE SCRIPT]

Return 8-12 beats. Each beat must include:
- start_second
- end_second
- narration
- visual_action
- camera
- motion_choreography
- on_screen_text
- sfx
- image_prompt
- video_prompt

Style:
Semi-realistic viral 3D simulation, bright readable vertical phone video, soft-featured uncanny CGI person, real-looking props, one almost-impossible physical danger, one survival rule, strong object motion, no cinematic darkness, no children's-cartoon look.

Camera:
Every beat needs timed camera choreography. The camera should move to the exact object or action the voice is describing.

Example:
0-1s push toward the hand; 1-3s snap zoom to the knife cutting the bacon; 3-5s whip pan to the bacon pieces sliding across the board.

Do not ask the video model to generate readable text. All readable text will be added later in editing.
```

## Image Prompt Template

```text
Vertical 9:16 semi-realistic viral 3D simulation explainer frame for a YouTube Short.

Scene:
[ROOM / TRAP]

Main subject:
Soft-featured slightly uncanny CGI avatar, smooth expressive face, believable hands, stylized but believable hair, simple modern clothing, readable silhouette, [POSE], [CLOTHING COLOR].

Environment:
[ROOM SHAPE], [DANGER OBJECT], [EXIT OBJECT], [RULE OBJECT].

Composition:
Phone-readable, centered action, high contrast, bright lighting, clear foreground object, safe empty space for large overlay text.

Style:
Polished game-engine-like 3D, semi-realistic social-video CGI, realistic props, crisp edges, colorful rule-based design, not cinematic, not horror, not children's animation, no gore, no logos, no readable text.

Negative:
photorealistic face, children's cartoon, Pixar character, nursery colors, dark moody film, gore, clutter, tiny details, fake text, captions, subtitles, watermark, logo, real person likeness.
```

## Video Prompt Template

```text
Vertical 9:16 semi-realistic viral 3D simulation explainer video, 1080p.

Action:
[ONE PHYSICAL ACTION THAT MOVES]

Scene:
[ROOM / TRAP DESCRIPTION]

Character:
Soft-featured slightly uncanny CGI avatar with smooth expressive face, believable hands, stylized but believable hair, simple modern clothing, [POSITION / ACTION].

Camera:
[front view / top-down / closeup / push-in], phone-readable, centered subject.

Motion:
[WHAT MOVES: water rises, sand pours, timer flashes, door slams, walls close, button depresses].

Motion choreography:
0-1s [camera starts on first named object]; 1-3s [camera moves to exact narrated action]; 3-5s [camera reveals consequence or reaction].

Style:
Bright semi-realistic viral 3D simulation explainer, clear shapes, high contrast, fast readable visual, polished game-engine render, realistic props, not cinematic horror, not children's animation, no readable text, no logos, no gore.
```

## Negative Video Prompt

```text
dark cinematic horror, children's cartoon, Pixar character, toy mascot, nursery colors, photorealistic victim, gore, blood, realistic injury, unreadable text, fake subtitles, logos, watermark, cluttered machinery, tiny background details, shaky camera, slow film trailer shot, real person likeness
```

## Thumbnail / Cover Prompt

```text
Vertical YouTube Shorts cover frame, semi-realistic viral 3D explainer style.

Topic:
[TOPIC]

Visual:
One simple human figure trapped in [ROOM], huge [DANGER OBJECT], visible [EXIT OBJECT], strong red/yellow/green rule colors, bright high contrast, centered composition, phone-readable.

Text area:
Leave clean space for large overlay text: [TEXT].

Style:
Polished semi-realistic social-video CGI, bold, readable, viral Shorts cover, no clutter, no gore, no logos, no fake text.
```

## Title Generation Prompt

```text
Generate 20 YouTube Shorts titles for Exit Scenario.

Rules:
- Second-person or object-first.
- 35-60 characters when possible.
- Clear trap or rule.
- No fake true story.
- No vague mystery words.
- No clickbait that the video cannot pay off.

Use these formulas:
- What Happens If [Physical Danger]
- How To Survive [Weird Situation]
- You Wake Up in [Impossible Place]
- This [Object] Has No [Expected Feature]
- You Have [Time] Before [Threat]
- The [Object] Only Opens When [Rule]
- You Must Choose One of [Number] [Objects]
- Do Not [Simple Action]
- The [Object] Is Lying

Topic bucket:
[BUCKET]
```

## Example Filled Concept

Title:
What Happens If You Are Trapped Under Ice

Hook:
You fall through thin ice, and the hole disappears behind you.

Rule:
Swim toward the darkest patch, because that is where the ice is open.

Escalation:
Your coat fills with water, your breath turns into bubbles, and the bright snow above leads the wrong way.

Solution:
Grab the edge, spread your arms, stay flat, and roll away instead of standing.

Twist:
The camera drops below the ice and shows something moving under the hole.
