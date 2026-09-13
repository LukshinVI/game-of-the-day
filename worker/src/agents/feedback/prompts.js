export const DETECTION_PROMPT = `
Ты анализируешь одно короткое сообщение
родителя о реакции ребёнка на активность.

Твоя задача:

1. Найти НЕЗАВИСИМЫЕ semantic signals
   в полном feedback.

2. Для каждого semantic signal выбрать
   один source_candidate, который является
   evidence этого сигнала.

3. Если CLEAR NEGATIVE имеет явно указанную
   причину, определить negative_reason
   и выбрать соответствующий reason_candidate.

ВАЖНО:

source_candidates — НЕ список сигналов.

Это точные фрагменты исходного feedback,
которые приложение разрешило использовать
как evidence semantic signals.

reason_candidates — отдельный список
возможных причин отрицательной реакции.

reason_candidates НЕ являются
самостоятельными semantic signals.

Нельзя создавать reaction signal
только потому, что текст присутствует
в reason_candidates.

Несколько candidates могут описывать
один semantic signal.

В таком случае верни только ОДИН signal.

Не дублируй один semantic факт
в нескольких signals.

Для короткого feedback обычно достаточно
1–3 независимых signals.

ПОВЕДЕНИЕ НЕ РАВНО РЕАКЦИИ.

Факт выполнения действия сам по себе
не доказывает положительную реакцию.

Факт прекращения действия сам по себе
не доказывает отрицательную реакцию.

Ты НЕ создаёшь evidence-текст.

evidence_candidate_id может быть только
candidate_id из source_candidates.

reason_candidate_id может быть только
candidate_id из reason_candidates.

Нельзя:

— придумывать evidence;
— перефразировать feedback;
— создавать signal на каждый candidate;
— возвращать несколько signals
  для одного semantic факта;
— превращать reason_candidate
  в отдельную реакцию;
— копировать evidence из примеров prompt;
— использовать metadata игры.

evidence_type:

EXPLICIT_EVALUATION

— родитель прямо сообщает оценку:
  понравилось, не понравилось,
  надоело и т.п.

AFFECT

— наблюдаемая эмоциональная реакция:
  смеялся, радовался,
  плакал, злился и т.п.

VOLUNTARY_ENGAGEMENT

— ребёнок добровольно продолжил,
  вернулся, попросил ещё
  или самостоятельно снова начал
  взаимодействие.

BEHAVIOR_ONLY

— описано действие ребёнка,
  но нет достаточного evidence
  оценки, эмоции или добровольной
  инициативы.

VOLUNTARY_ENGAGEMENT требует
явного основания инициативы.

engagement_basis:

REQUESTED_MORE
— ребёнок попросил продолжить,
  повторить или дать ещё.

RETURNED_VOLUNTARILY
— ребёнок добровольно вернулся.

CONTINUED_VOLUNTARILY
— ребёнок добровольно продолжал.

RESTARTED_VOLUNTARILY
— ребёнок самостоятельно снова
  начал действие.

NONE
— для всех остальных evidence_type.

Если нельзя выбрать конкретный
engagement_basis,
не используй VOLUNTARY_ENGAGEMENT.

Примеры:

"Строил башню"

→ BEHAVIOR_ONLY
→ engagement_basis=NONE
→ reaction_status=NONE
→ polarity=NONE

"Повторял за мной"

→ BEHAVIOR_ONLY,
если нет evidence самостоятельной
инициативы.

"Попросил повторить песенку"

→ VOLUNTARY_ENGAGEMENT
→ REQUESTED_MORE
→ CLEAR
→ POSITIVE

"Сам снова начал"

→ VOLUNTARY_ENGAGEMENT
→ RESTARTED_VOLUNTARILY
→ CLEAR
→ POSITIVE

"Ребёнку понравилось"

→ EXPLICIT_EVALUATION
→ CLEAR
→ POSITIVE
→ engagement_basis=NONE

"Не понравилось"

→ EXPLICIT_EVALUATION
→ CLEAR
→ NEGATIVE
→ engagement_basis=NONE

reaction_status:

CLEAR
— реакция определена.

AMBIGUOUS
— feedback прямо сообщает,
  что реакцию определить нельзя.

NONE
— evidence реакции отсутствует.

polarity:

POSITIVE
NEGATIVE
NONE

BEHAVIOR_ONLY всегда:

reaction_status=NONE
polarity=NONE
engagement_basis=NONE.

AMBIGUOUS всегда:

polarity=NONE.

Пример:

"Не поняла, понравилось ему или нет"

→ AMBIGUOUS
→ polarity=NONE.

Простое отсутствие реакции
не является AMBIGUOUS.

Для CLEAR или AMBIGUOUS
evidence_candidate_id обязателен.

Пример полного feedback:

"Строил башню,
потом попросил повторить песенку"

Здесь ДВА semantic signals:

1.

"Строил башню"

→ BEHAVIOR_ONLY
→ NONE / NONE
→ engagement_basis=NONE

2.

"попросил повторить песенку"

→ VOLUNTARY_ENGAGEMENT
→ CLEAR / POSITIVE
→ engagement_basis=REQUESTED_MORE

Не считай "Строил башню"
VOLUNTARY_ENGAGEMENT только потому,
что позже в сообщении есть просьба.

Для CLEAR NEGATIVE определи
negative_reason:

GAME_DISLIKE
DIFFICULTY
TEMPORARY_STATE
EXTERNAL_FACTOR
OTHER_EXPLICIT
UNSPECIFIED

Если причина CLEAR NEGATIVE
явно содержится в reason_candidates,
выбери один reason_candidate_id,
который лучше всего доказывает причину.

Пример:

feedback:

"Расстроился и заплакал,
потому что башня всё время падала"

source_candidates:

"Расстроился и заплакал"

reason_candidates:

"башня всё время падала"

Правильно:

один AFFECT signal
→ CLEAR
→ NEGATIVE
→ negative_reason=DIFFICULTY
→ evidence = "Расстроился и заплакал"
→ reason = "башня всё время падала"

НЕ создавай второй NEGATIVE signal
из reason_candidate.

Если NEGATIVE известен,
но причина не указана:

negative_reason=UNSPECIFIED
reason_candidate_id=null.

Для всех сигналов,
которые не являются CLEAR NEGATIVE:

negative_reason=NOT_APPLICABLE
reason_candidate_id=null.

Не определяй target.

Не определяй preference.

Верни только минимальный набор
независимых semantic signals.
`.trim();

export const TARGET_LINKING_PROMPT = `
Ты выполняешь только semantic target linking.

Реакции уже найдены предыдущим этапом.

Не классифицируй их заново.

Для каждого переданного signal
нужно вернуть РОВНО ОДИН link.

Ты НЕ создаёшь target-текст.

Ты можешь выбирать только
candidate_id из списка source_candidates,
который передало приложение.

Каждый source_candidate уже является
точным фрагментом исходного feedback.

status:

RESOLVED

— один candidate однозначно является
  объектом, действием или активностью,
  к которым относится сигнал.

UNSPECIFIED

— реакция известна,
  но конкретный target нельзя
  надёжно определить.

AMBIGUOUS

— существуют минимум два
  правдоподобных candidate,
  и текст не позволяет выбрать один.

Правила candidate_ids:

RESOLVED
→ ровно один candidate_id.

UNSPECIFIED
→ пустой массив.

AMBIGUOUS
→ минимум два candidate_id.

Выбирай наиболее короткий candidate,
который полностью сохраняет смысл target.

Не выбирай:

— эмоциональную реакцию;
— оценочную фразу;
— причину эмоции;
— сам способ участия;
— generic слова вроде "действие" или "игра";
— другой объект или действие,
  просто потому что он упомянут рядом.

Пример:

"попросил повторить песенку"

reaction / engagement:

"попросил повторить"

target:

"песенку".

Пример:

"Строил башню,
потом попросил повторить песенку"

для сигнала про просьбу повторить
target относится к "песенке",
а не к "башне".

Если надёжного candidate нет:

UNSPECIFIED.

Не придумывай новые candidate_id.

Не используй metadata.

Для КАЖДОГО входного signal_id
обязательно верни один link.
`.trim();