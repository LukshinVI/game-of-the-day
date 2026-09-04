export const feedbackCases = [
    {
        id: 'explicit-like',
        description: 'Явная положительная реакция на конкретное действие',
        input: {
            game: {
                id: '101',
                title: 'Башня из стаканчиков',
                skill: 'координация',
                mechanic: 'строительство башни'
            },
            feedback: {
                text: 'Очень понравилось строить башню, несколько раз просил повторить'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 1,
                negative: 0,
                unresolved: 0
            },
            targetChecks: [
                {
                    reaction: 'POSITIVE',
                    contains: 'башн'
                }
            ]
        }
    },

    {
        id: 'explicit-dislike',
        description: 'Явная отрицательная реакция на конкретное действие',
        input: {
            game: {
                id: '102',
                title: 'Башня из стаканчиков',
                skill: 'координация',
                mechanic: 'строительство башни'
            },
            feedback: {
                text: 'Башня быстро надоела, больше строить не захотел'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 0,
                negative: 1,
                unresolved: 0
            }
        }
    },

    {
        id: 'mixed-feedback',
        description: 'Есть положительная и отрицательная реакции на разные действия',
        input: {
            game: {
                id: '103',
                title: 'Башня из стаканчиков',
                skill: 'координация',
                mechanic: 'строительство башни'
            },
            feedback: {
                text: 'Башня ему быстро надоела, зато очень понравилось складывать стаканчики друг в друга'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 1,
                negative: 1,
                unresolved: 0
            }
        }
    },

    {
        id: 'behavioral-like',
        description: 'Положительная реакция выражена только поведением',
        input: {
            game: {
                id: '104',
                title: 'Весёлая игра',
                skill: 'координация',
                mechanic: 'манипуляции с предметами'
            },
            feedback: {
                text: 'Когда я стала прятать кубик под платок, начал сам его искать и просил повторить'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 1,
                negative: 0,
                unresolved: 0
            }
        }
    },

    {
        id: 'behavioral-dislike',
        description: 'Отрицательная реакция выражена только поведением',
        input: {
            game: {
                id: '105',
                title: 'Катим мяч',
                skill: 'координация',
                mechanic: 'перекатывание мяча'
            },
            feedback: {
                text: 'Пару раз толкнул мяч, потом отвернулся и больше к нему не возвращался'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 0,
                negative: 1,
                unresolved: 0
            },
            targetChecks: [
                {
                    reaction: 'NEGATIVE',
                    contains: 'мяч'
                }
            ]
        }
    },

    {
        id: 'no-preference',
        description: 'Игра состоялась, но реакции-предпочтения нет',
        input: {
            game: {
                id: '106',
                title: 'Попади в коробку',
                skill: 'мелкая моторика',
                mechanic: 'складывание предметов в контейнер'
            },
            feedback: {
                text: 'Сыграли минут пять, всё получилось'
            }
        },
        expected: {
            decision: 'NO_PREFERENCE',
            observations: {
                positive: 0,
                negative: 0,
                unresolved: 0
            }
        }
    },

    {
        id: 'needs-clarification',
        description: 'Есть отрицательная реакция, но её target неизвестен',
        input: {
            game: {
                id: '107',
                title: 'Катим мяч',
                skill: 'координация',
                mechanic: 'перекатывание мяча'
            },
            feedback: {
                text: 'Сегодня вообще не пошло'
            }
        },
        expected: {
            decision: 'NEED_CLARIFICATION',
            observations: {
                positive: 0,
                negative: 1,
                unresolved: 1
            }
        }
    },

    {
        id: 'new-mechanic',
        description: 'Реакция относится к действию, которого нет в metadata игры',
        input: {
            game: {
                id: '108',
                title: 'Игра с коробкой',
                skill: 'мелкая моторика',
                mechanic: 'складывание предметов в коробку'
            },
            feedback: {
                text: 'В коробку почти ничего не складывал, зато постоянно открывал и закрывал крышку и смеялся, когда она хлопала'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 1,
                negative: 1,
                unresolved: 0
            }
        }
    },

    // Regression cases

    {
        id: 'neutral-single-action',
        description: 'Сам факт выполнения действия не является положительной реакцией',
        input: {
            game: {
                id: '201',
                title: 'Кубик в коробке',
                skill: 'мелкая моторика',
                mechanic: 'складывание предметов'
            },
            feedback: {
                text: 'Положил кубик в коробку один раз'
            }
        },
        expected: {
            decision: 'NO_PREFERENCE',
            observations: {
                positive: 0,
                negative: 0,
                unresolved: 0
            }
        }
    },

    {
        id: 'external-distraction',
        description: 'Внешнее отвлечение не должно превращаться в негатив к действию',
        input: {
            game: {
                id: '202',
                title: 'Катим мяч',
                skill: 'координация',
                mechanic: 'перекатывание мяча'
            },
            feedback: {
                text: 'Толкнул мяч, потом отвернулся, потому что увидел кошку'
            }
        },
        expected: {
            decision: 'NO_PREFERENCE',
            observations: {
                positive: 0,
                negative: 0,
                unresolved: 0
            }
        }
    },

    {
        id: 'target-switch',
        description: 'Новый target не должен наследоваться от предыдущего действия',
        input: {
            game: {
                id: '203',
                title: 'Башня из стаканчиков',
                skill: 'координация',
                mechanic: 'строительство башни'
            },
            feedback: {
                text: 'Строил башню, потом попросил повторить песенку'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 1,
                negative: 0,
                unresolved: 0
            },
            targetChecks: [
                {
                    reaction: 'POSITIVE',
                    contains: 'песен'
                }
            ]
        }
    },

    {
        id: 'self-repeat-positive',
        description: 'Самостоятельное повторение действия является положительным сигналом',
        input: {
            game: {
                id: '204',
                title: 'Коробка с крышкой',
                skill: 'мелкая моторика',
                mechanic: 'открывание крышки'
            },
            feedback: {
                text: 'Открыл крышку один раз, потом сам снова открыл её несколько раз'
            }
        },
        expected: {
            decision: 'PREFERENCE_DETECTED',
            observations: {
                positive: 1,
                negative: 0,
                unresolved: 0
            },
            targetChecks: [
                {
                    reaction: 'POSITIVE',
                    contains: 'крыш'
                }
            ]
        }
    }
];