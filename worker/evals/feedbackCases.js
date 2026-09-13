export const feedbackCases = [
    {
        id: 'explicit-like',

        description:
            'Явная положительная реакция',

        input: {
            feedback: {
                text:
                    'Очень понравилось строить башню, несколько раз просил повторить'
            }
        },

        expected: {
            decision:
                'OBSERVATION_RECORDED',

            positive: 2,
            negative: 0,
            ambiguous: 0,

            clarification:
                false
        }
    },

    {
        id: 'explicit-dislike',

        description:
            'Явный dislike игры',

        input: {
            feedback: {
                text:
                    'Башня быстро надоела, больше строить не захотел'
            }
        },

        expected: {
            decision:
                'NEED_CLARIFICATION',

            positive: 0,
            negative: 1,
            ambiguous: 0,

            clarification:
                true,

            clarificationType:
                'NEGATIVE_REASON',

            negativeReason:
                'GAME_DISLIKE'
        }
    },

    {
        id: 'mixed-feedback',

        description:
            'Положительная и отрицательная реакции',

        input: {
            feedback: {
                text:
                    'Башня ему быстро надоела, зато очень понравилось складывать стаканчики друг в друга'
            }
        },

        expected: {
            decision:
                'NEED_CLARIFICATION',

            positive: 1,

            negative: 1,

            ambiguous: 0,

            clarification:
                true,

            clarificationType:
                'NEGATIVE_REASON'

        }
    },

    {
        id: 'behavioral-like',

        description:
            'Добровольная вовлечённость',

        input: {
            feedback: {
                text:
                    'Когда я стала прятать кубик под платок, начал сам его искать и просил повторить'
            }
        },

        expected: {
            decision:
                'OBSERVATION_RECORDED',

            positive: 1,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            evidenceType:
                'VOLUNTARY_ENGAGEMENT'
        }
    },

    {
        id: 'behavioral-dislike',

        description:
            'Прекращение участия не равно NEGATIVE',

        input: {
            feedback: {
                text:
                    'Пару раз толкнул мяч, потом отвернулся и больше к нему не возвращался'
            }
        },

        expected: {
            decision:
                'NO_REACTION_EVIDENCE',

            positive: 0,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            evidenceType:
                'BEHAVIOR_ONLY'
        }
    },

    {
        id: 'no-preference',

        description:
            'Нет reaction evidence',

        input: {
            feedback: {
                text:
                    'Сыграли минут пять, всё получилось'
            }
        },

        expected: {
            decision:
                'NO_REACTION_EVIDENCE',

            positive: 0,
            negative: 0,
            ambiguous: 0,

            clarification:
                false
        }
    },

    {
        id: 'needs-clarification',

        description:
            'Негатив есть, причина неизвестна',

        input: {
            feedback: {
                text:
                    'Сегодня вообще не пошло'
            }
        },

        expected: {
            decision:
                'NEED_CLARIFICATION',

            positive: 0,
            negative: 1,
            ambiguous: 0,

            clarification:
                true,

            clarificationType:
                'NEGATIVE_REASON',

            negativeReason:
                'UNSPECIFIED'
        }
    },

    {
        id: 'new-mechanic',

        description:
            'Неудача действия не является негативом',

        input: {
            feedback: {
                text:
                    'В коробку почти ничего не складывал, зато постоянно открывал и закрывал крышку и смеялся, когда она хлопала'
            }
        },

        expected: {
            decision:
                'OBSERVATION_RECORDED',

            positive: 1,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            evidenceType:
                'AFFECT'
        }
    },

    {
        id: 'neutral-single-action',

        description:
            'Факт действия — behavior only',

        input: {
            feedback: {
                text:
                    'Положил кубик в коробку один раз'
            }
        },

        expected: {
            decision:
                'NO_REACTION_EVIDENCE',

            positive: 0,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            evidenceType:
                'BEHAVIOR_ONLY'
        }
    },

    {
        id: 'external-distraction',

        description:
            'Внешнее отвлечение не равно dislike',

        input: {
            feedback: {
                text:
                    'Толкнул мяч, потом отвернулся, потому что увидел кошку'
            }
        },

        expected: {
            decision:
                'NO_REACTION_EVIDENCE',

            positive: 0,
            negative: 0,
            ambiguous: 0,

            clarification:
                false
        }
    },

    {
        id: 'target-switch',

        description:
            'Положительная реакция относится к песенке',

        input: {
            feedback: {
                text:
                    'Строил башню, потом попросил повторить песенку'
            }
        },

        expected: {
            decision:
                'OBSERVATION_RECORDED',

            positive: 1,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            targetContains:
                'песен'
        }
    },

    {
        id: 'self-repeat-positive',

        description:
            'Самостоятельное повторение',

        input: {
            feedback: {
                text:
                    'Открыл крышку один раз, потом сам снова открыл её несколько раз'
            }
        },

        expected: {
            decision:
                'OBSERVATION_RECORDED',

            positive: 1,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            evidenceType:
                'VOLUNTARY_ENGAGEMENT',

            targetContains:
                'крыш'
        }
    },

    {
        id: 'production-generic-positive',

        description:
            'Generic feedback не получает target из metadata',

        input: {
            game: {
                title:
                    'Ладушки вместе',

                mechanic:
                    'хлопки'
            },

            feedback: {
                text:
                    'Ребёнку понравилось, несколько раз сам повторял действие'
            }
        },

        expected: {
            decision:
                'OBSERVATION_RECORDED',

            positive: 2,
            negative: 0,
            ambiguous: 0,

            clarification:
                false,

            targetStatus:
                'UNSPECIFIED',

            forbiddenTargets: [
                'Ладушки',
                'хлоп'
            ]
        }
    },

    {
        id: 'negative-difficulty',

        description:
            'Негатив вызван сложностью',

        input: {
            feedback: {
                text:
                    'Расстроился и заплакал, потому что башня всё время падала и не получалось её построить'
            }
        },

        expected: {
            decision:
                'NEED_CLARIFICATION',

            positive: 0,
            negative: 1,
            ambiguous: 0,

            clarification:
                true,

            clarificationType:
                'NEGATIVE_REASON',

            negativeReason:
                'DIFFICULTY'
        }
    },

    {
        id: 'negative-unspecified',

        description:
            'Негатив без причины требует ASK',

        input: {
            feedback: {
                text:
                    'Ему совсем не зашло сегодня'
            }
        },

        expected: {
            decision:
                'NEED_CLARIFICATION',

            positive: 0,
            negative: 1,
            ambiguous: 0,

            clarification:
                true,

            clarificationType:
                'NEGATIVE_REASON'
        }
    },

    {
        id: 'ambiguous-reaction',

        description:
            'Реальная неоднозначность реакции',

        input: {
            feedback: {
                text:
                    'Вроде заинтересовался, но я так и не поняла, понравилось ему или нет'
            }
        },

        expected: {
            decision:
                'NEED_CLARIFICATION',

            positive: 0,
            negative: 0,
            ambiguous: 1,

            clarification:
                true,

            clarificationType:
                'REACTION'
        }
    }
];