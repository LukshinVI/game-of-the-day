export const DETECTION_PROMPT = `
Ты классифицируешь уже выделенные
структурные сегменты сообщения родителя
о реакции ребёнка на активность.

Приложение уже выполнило segmentation.

ТЫ НЕ ИЩЕШЬ новые фрагменты текста.
ТЫ НЕ СОЗДАЁШЬ новые semantic signals.
ТЫ НЕ ОБЪЕДИНЯЕШЬ segments.
ТЫ НЕ ДЕЛИШЬ segments.

Для КАЖДОГО входного segment
верни РОВНО ОДНУ classification.

segment_id должен быть скопирован
без изменений.

Нельзя пропускать segment.
Нельзя возвращать неизвестный segment_id.
Нельзя возвращать segment дважды.

relations описывают только
структурные отношения между segments.

CAUSE:

from_segment_id является причиной
to_segment_id.

CONTRAST:

segments структурно противопоставлены.

--------------------------------
EVIDENCE TYPE
--------------------------------

EXPLICIT_EVALUATION

Родитель прямо сообщает оценку
реакции ребёнка:

понравилось,
не понравилось,
не зашло и т.п.

AFFECT

Наблюдаемая эмоциональная реакция:

радовался,
смеялся,
расстроился,
плакал и т.п.

VOLUNTARY_ENGAGEMENT

Есть evidence добровольной инициативы:

ребёнок попросил ещё,
добровольно вернулся,
продолжил,
сам снова начал действие.

BEHAVIOR_ONLY

Описано поведение,
но этого недостаточно,
чтобы утверждать оценку,
эмоцию или добровольную инициативу.

--------------------------------
ВАЖНО
--------------------------------

ПОВЕДЕНИЕ НЕ РАВНО РЕАКЦИИ.

"Строил башню"

→ BEHAVIOR_ONLY
→ reaction_status=NONE
→ polarity=NONE

"Толкнул мяч"

→ BEHAVIOR_ONLY
→ NONE / NONE

"Отвернулся"

само по себе:

→ BEHAVIOR_ONLY
→ NONE / NONE

"Больше не возвращался"

само по себе:

→ BEHAVIOR_ONLY
→ NONE / NONE

Прекращение участия само по себе
НЕ является NEGATIVE.

--------------------------------
VOLUNTARY ENGAGEMENT
--------------------------------

VOLUNTARY_ENGAGEMENT допустим только
при evidence добровольной инициативы.

engagement_basis:

REQUESTED_MORE

ребёнок попросил ещё
или попросил повторить.

RETURNED_VOLUNTARILY

ребёнок добровольно вернулся.

CONTINUED_VOLUNTARILY

ребёнок явно добровольно продолжал.

RESTARTED_VOLUNTARILY

ребёнок самостоятельно снова
начал действие.

NONE

для всех остальных evidence_type.

"Повторял за мной"

не доказывает инициативу:

→ BEHAVIOR_ONLY.

"Сам снова начал"

→ VOLUNTARY_ENGAGEMENT
→ RESTARTED_VOLUNTARILY
→ CLEAR / POSITIVE.

"Несколько раз сам повторял действие"

→ VOLUNTARY_ENGAGEMENT
→ CLEAR / POSITIVE.

--------------------------------
REACTION STATUS
--------------------------------

CLEAR

реакция определена.

AMBIGUOUS

текст прямо показывает,
что реакцию нельзя уверенно определить.

NONE

evidence реакции нет.

AMBIGUOUS всегда:

polarity=NONE.

Пример:

"понравилось ему или нет"

→ EXPLICIT_EVALUATION
→ AMBIGUOUS
→ polarity=NONE.

Фраза:

"я не поняла"

сама по себе не является
отдельной реакцией ребёнка.

Если в таком segment нет
самостоятельного evidence реакции:

→ BEHAVIOR_ONLY
→ NONE / NONE.

--------------------------------
POLARITY
--------------------------------

POSITIVE
NEGATIVE
NONE

CLEAR требует
POSITIVE или NEGATIVE.

AMBIGUOUS требует NONE.

NONE требует NONE.

BEHAVIOR_ONLY всегда:

reaction_status=NONE
polarity=NONE
engagement_basis=NONE.

--------------------------------
EXPERIENCER
--------------------------------

CHILD

реакция относится к ребёнку.

OTHER

реакция явно относится
к другому человеку.

UNKNOWN

нельзя надёжно определить.

--------------------------------
CAUSE
--------------------------------

Если segment является
from_segment_id отношения CAUSE,
определи cause_type.

cause_type:

DIFFICULTY

причина связана со сложностью,
неудачей выполнения,
невозможностью справиться.

TEMPORARY_STATE

усталость,
сонливость,
голод,
болезненное или иное
временное состояние ребёнка.

EXTERNAL_FACTOR

причина вне самой активности:

шум,
отвлечение,
другой человек,
внешнее событие.

GAME_DISLIKE

текст причины прямо говорит,
что ребёнку не понравилась
сама активность или её элемент.

OTHER_EXPLICIT

причина явно указана,
но не относится
к категориям выше.

UNSPECIFIED

segment структурно является причиной,
но её смысл нельзя уверенно
классифицировать.

Для segment,
который НЕ является
from_segment_id отношения CAUSE:

cause_type=NOT_APPLICABLE.

CAUSE segment может одновременно
описывать обычное поведение.

Например:

feedback:
"Расстроился и заплакал,
потому что башня всё время падала"

relations:
segment-2 CAUSE → segment-1

segment-1:

AFFECT
CLEAR
NEGATIVE
cause_type=NOT_APPLICABLE

segment-2:

BEHAVIOR_ONLY
NONE
NONE
cause_type=DIFFICULTY

Не создавай из segment-2
вторую NEGATIVE reaction.

--------------------------------
ПРИМЕРЫ
--------------------------------

"Ребёнку понравилось"

→ EXPLICIT_EVALUATION
→ CLEAR
→ POSITIVE.

"Ему совсем не зашло сегодня"

→ EXPLICIT_EVALUATION
→ CLEAR
→ NEGATIVE.

"Расстроился и заплакал"

→ AFFECT
→ CLEAR
→ NEGATIVE.

"несколько раз сам повторял действие"

→ VOLUNTARY_ENGAGEMENT
→ CLEAR
→ POSITIVE.

Не определяй target.

Не определяй preference.

Не используй metadata игры.

Верни classifications
ровно для всех входных segments.
`.trim();