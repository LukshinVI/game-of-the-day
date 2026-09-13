export const DETECTION_SCHEMA = {
    type:
        'object',

    properties: {
        classifications: {
            type:
                'array',

            items: {
                type:
                    'object',

                properties: {
                    segment_id: {
                        type:
                            'string'
                    },

                    evidence_type: {
                        type:
                            'string',

                        enum: [
                            'EXPLICIT_EVALUATION',
                            'AFFECT',
                            'VOLUNTARY_ENGAGEMENT',
                            'BEHAVIOR_ONLY'
                        ]
                    },

                    reaction_status: {
                        type:
                            'string',

                        enum: [
                            'CLEAR',
                            'AMBIGUOUS',
                            'NONE'
                        ]
                    },

                    polarity: {
                        type:
                            'string',

                        enum: [
                            'POSITIVE',
                            'NEGATIVE',
                            'NONE'
                        ]
                    },

                    experiencer: {
                        type:
                            'string',

                        enum: [
                            'CHILD',
                            'OTHER',
                            'UNKNOWN'
                        ]
                    },

                    engagement_basis: {
                        type:
                            'string',

                        enum: [
                            'REQUESTED_MORE',
                            'RETURNED_VOLUNTARILY',
                            'CONTINUED_VOLUNTARILY',
                            'RESTARTED_VOLUNTARILY',
                            'NONE'
                        ]
                    },

                    cause_type: {
                        type:
                            'string',

                        enum: [
                            'DIFFICULTY',
                            'TEMPORARY_STATE',
                            'EXTERNAL_FACTOR',
                            'GAME_DISLIKE',
                            'OTHER_EXPLICIT',
                            'UNSPECIFIED',
                            'NOT_APPLICABLE'
                        ]
                    }
                },

                required: [
                    'segment_id',
                    'evidence_type',
                    'reaction_status',
                    'polarity',
                    'experiencer',
                    'engagement_basis',
                    'cause_type'
                ],

                additionalProperties:
                    false
            }
        }
    },

    required: [
        'classifications'
    ],

    additionalProperties:
        false
};