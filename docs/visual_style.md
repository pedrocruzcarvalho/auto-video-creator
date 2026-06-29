# Exit Scenario Visual Style

## Format

- Aspect ratio: 9:16 vertical.
- Target resolution: 1080x1920.
- Design for phone viewing first.
- Keep important action in the center 70% of the frame.
- Leave safe space for captions and big labels.

## Style Direction

Semi-realistic viral 3D simulation explainer, not film trailer and not children's animation.

The look should be:

- simple;
- brightly lit;
- readable;
- slightly uncanny;
- semi-realistic;
- game-engine polished;
- realistic-prop driven;
- physical and object-driven;
- fast to understand at thumbnail size.

The viewer should understand the room, rule, and threat immediately.

## Character Design

Default character:

- semi-realistic soft-featured 3D human avatar;
- smooth expressive face;
- readable eyes;
- stylized but believable hair;
- real-looking hands;
- clean digital skin shader with low pore detail;
- simple modern clothing;
- solid readable silhouette;
- readable exaggerated expression or gesture;
- mildly uncanny but approachable social-video CGI finish.

Avoid:

- realistic human likenesses;
- faceless mannequin characters as the main style;
- celebrity faces;
- Pixar/cartoon proportions;
- children's-video character design;
- toy-like comedy mascots;
- anime;
- claymation;
- gore;
- complex clothing;
- horror monster design unless approved for a specific episode.

## Environment Design

Every environment must have:

- one dominant room shape;
- one main danger;
- one escape object;
- one clear rule indicator.
- enough empty space that the action reads instantly on mobile.

Useful props:

- water tank;
- ice sheet;
- elevator doors;
- freezer door;
- kitchen table;
- glass container;
- cutaway anatomy prop;
- door;
- timer;
- button;
- keypad;
- red alarm light;
- water line;
- sand chute;
- oxygen gauge;
- cracked glass;
- pressure meter;
- exit sign;
- floor panel;
- lever.

## Camera Rules

Use simple phone-readable shots, but keep the camera moving. The camera should behave like an editor's pointer: whenever the narration names an object or action, the camera moves to that object or action immediately.

Preferred shots:

- front view of the trap;
- top-down map-like view;
- closeup of button/gauge/timer;
- macro closeup of hands, tools, water, ice, fabric, valves, or pressure points;
- cutaway simulation showing what is happening inside an object or body;
- side view showing distance closing;
- over-shoulder view facing the exit;
- fast push-in for the twist.
- simple simulation/cutaway shot showing the mechanism.
- snap zoom to the exact object mentioned by the voice;
- whip pan from character reaction to the danger object;
- rack focus from tool/object to consequence;
- top-down drop onto the action;
- orbit around the main object while it moves.

Avoid:

- slow cinematic crane shots;
- shallow-focus beauty shots;
- dark silhouettes where the rule is unclear;
- complicated camera movement that does not point to the narrated detail;
- long shots with tiny action.
- stale still plates with no object or character motion.

## Motion Choreography

Every generated video beat needs a timed camera path.

Template:

```text
0-1s push toward [person/object named first];
1-3s snap zoom or macro track to [action named by narration];
3-5s whip pan / cutaway / reaction shot showing [consequence].
```

Good:

```text
0-1s push toward the chef's hand; 1-3s snap zoom to the knife slicing bacon; 3-5s whip pan to the bacon cubes sliding across the board.
```

Bad:

```text
Camera shows the kitchen while the chef prepares food.
```

## Color System

Use color as rule language.

- Red: danger, wrong choice, countdown, alarm.
- Green: exit, safe choice, unlocked.
- Yellow: warning, timer, clue.
- Blue: water, cold, oxygen, glass.
- White: clean room, neutral surface, readable contrast.
- Black: void, final twist, inactive door.

Do not make the entire video dark. Darkness can appear only for a twist or brief danger beat.

## Visual Beat Rhythm

For a 35-55 second Short:

- 0s: full trap visible, already moving.
- 2s: rule object visible.
- 5s: closeup of timer/rule.
- 9s: danger begins.
- 14s: wrong option or threat grows.
- 20s: character notices clue.
- 28s: solution action starts.
- 36s: exit reacts.
- final beat: twist image with motion.

## Generated Video Prompt Rules

Prompts should be direct and physical.

Include:

- vertical 9:16;
- semi-realistic viral 3D simulation explainer style;
- soft-featured slightly uncanny CGI avatar;
- smooth expressive face, believable eyes, stylized hair;
- real-looking hands and props;
- main character position;
- room shape;
- one moving danger;
- one clear object;
- exact camera view;
- what moves during the clip.
- what the camera does: snap zoom, push-in, whip pan, top-down view, macro closeup, or cutaway.

Avoid asking the model to generate:

- readable signs;
- captions;
- subtitles;
- complex UI text;
- logos;
- real people;
- gore.

All readable text should be added later in editing.

Every paid render beat must be real video. Still images are not acceptable final footage.

## Negative Style

Avoid these phrases in final prompts unless intentionally testing:

- cinematic horror;
- dark moody film;
- photorealistic victim;
- faceless mannequin as the main character style;
- Pixar cartoon;
- children's-video CGI;
- nursery colors;
- toy character;
- anime;
- claymation;
- realistic gore;
- real-incident reenactment;
- historical archive;
- shallow depth of field;
- complex machinery everywhere;
- tiny background details;
- readable poster text.

## Thumbnail / Cover Frame

Each Short needs a readable cover frame.

Cover frame requirements:

- one large trap object;
- one character in danger;
- one large text phrase;
- high contrast;
- no clutter;
- threat visible without narration.

Recommended cover text:

- NO EXIT
- 60 SECONDS
- DON'T MOVE
- WRONG DOOR
- ROOM 12
- PICK ONE
