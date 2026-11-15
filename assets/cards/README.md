# Card illustrations

One illustration per skill (`skill_<key>.jpg`, keys from `src/preparation/skills.py`) and
one per occasion (`greet_<birthday|new_year|mothers_day|fathers_day>.jpg`, used on that day
instead of the skill picture when present),
shown above the game text by `src/delivery/cards.py` when `GAME_CARDS=1`. Telegram fetches
them from this directory's public URL, so a file here is live once it is on `main`. Generated for this project in one style (the knitted bunny
of the VK community cover); 1280×853 JPEG. No people, faces or text; only large objects.
