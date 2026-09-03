export const feedbackCases = [
  {
    id: 'explicit-like',
    description: 'Явное положительное предпочтение',
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
      liked: true,
      disliked: false
    }
  },

  {
    id: 'explicit-dislike',
    description: 'Явное отрицательное предпочтение',
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
      liked: false,
      disliked: true
    }
  },

  {
    id: 'mixed-feedback',
    description: 'Одна механика не понравилась, другая понравилась',
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
      liked: true,
      disliked: true
    }
  },

  {
    id: 'behavioral-like',
    description: 'Предпочтение выражено только поведением',
    input: {
      game: {
        id: '104',
        title: 'Весёлая игра',
        skill: 'координация',
        mechanic: 'манипуляции с предметами'
      },
      feedback: {
        text: 'Играть особо не хотел, но когда я стала прятать кубик под платок, начал сам его искать и просил повторить'
      }
    },
    expected: {
      decision: 'PREFERENCE_DETECTED',
      liked: true,
      disliked: false
    }
  },

  {
    id: 'behavioral-dislike',
    description: 'Негативное предпочтение выражено поведением',
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
      liked: false,
      disliked: true
    }
  },

  {
    id: 'no-preference',
    description: 'Игра состоялась, предпочтения нет',
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
      liked: false,
      disliked: false
    }
  },

  {
    id: 'needs-clarification',
    description: 'Есть негативная реакция, но причина непонятна',
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
      liked: false,
      disliked: false
    }
  },

  {
    id: 'new-mechanic',
    description: 'Понравилась механика, которой нет в metadata игры',
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
      liked: true,
      disliked: false
    }
  }
];