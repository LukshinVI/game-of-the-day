import {
    DETECTION_SCHEMA
} from './detectionSchema.js';

import {
    DETECTION_PROMPT
} from './detectionPrompt.js';

export async function runDetectionModel(
    env,
    model,
    segments,
    relations
) {
    return env.AI.run(
        model,
        {
            messages: [
                {
                    role:
                        'system',

                    content:
                        DETECTION_PROMPT
                },

                {
                    role:
                        'user',

                    content:
                        JSON.stringify({
                            segments:
                                segments.map(
                                    (
                                        segment
                                    ) => ({
                                        segment_id:
                                            segment
                                                .segment_id,

                                        text:
                                            segment
                                                .text
                                    })
                                ),

                            relations:
                                relations.map(
                                    (
                                        relation
                                    ) => ({
                                        type:
                                            relation
                                                .type,

                                        from_segment_id:
                                            relation
                                                .from_segment_id,

                                        to_segment_id:
                                            relation
                                                .to_segment_id
                                    })
                                )
                        })
                }
            ],

            response_format: {
                type:
                    'json_schema',

                json_schema:
                    DETECTION_SCHEMA
            },

            temperature:
                0
        }
    );
}