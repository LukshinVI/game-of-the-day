export const DETECTION_SCHEMA = {
    name:
        'feedback_detection',

    schema: {
        type:
            'object',

        additionalProperties:
            false,

        properties: {
            signals: {
                type:
                    'array',

                items: {
                    type:
                        'object',

                    additionalProperties:
                        false,

                    properties: {
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

                        evidence_candidate_id: {
                            type: [
                                'string',
                                'null'
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

                        negative_reason: {
                            type:
                                'string',

                            enum: [
                                'GAME_DISLIKE',
                                'DIFFICULTY',
                                'TEMPORARY_STATE',
                                'EXTERNAL_FACTOR',
                                'OTHER_EXPLICIT',
                                'UNSPECIFIED',
                                'NOT_APPLICABLE'
                            ]
                        },

                        reason_candidate_id: {
                            type: [
                                'string',
                                'null'
                            ]
                        }
                    },

                    required: [
                        'evidence_type',
                        'reaction_status',
                        'polarity',
                        'experiencer',
                        'evidence_candidate_id',
                        'engagement_basis',
                        'negative_reason',
                        'reason_candidate_id'
                    ]
                }
            }
        },

        required: [
            'signals'
        ]
    }
};

export const TARGET_SCHEMA = {
    name:
        'feedback_target_linking',

    schema: {
        type:
            'object',

        additionalProperties:
            false,

        properties: {
            links: {
                type:
                    'array',

                minItems:
                    1,

                items: {
                    type:
                        'object',

                    additionalProperties:
                        false,

                    properties: {
                        signal_id: {
                            type:
                                'string'
                        },

                        status: {
                            type:
                                'string',

                            enum: [
                                'RESOLVED',
                                'UNSPECIFIED',
                                'AMBIGUOUS'
                            ]
                        },

                        candidate_ids: {
                            type:
                                'array',

                            items: {
                                type:
                                    'string'
                            }
                        }
                    },

                    required: [
                        'signal_id',
                        'status',
                        'candidate_ids'
                    ]
                }
            }
        },

        required: [
            'links'
        ]
    }
};