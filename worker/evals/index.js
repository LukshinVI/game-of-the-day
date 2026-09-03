import { runFeedbackEvals } from './runFeedbackEvals.js';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname !== '/__evals/feedback') {
      return new Response('Not found', { status: 404 });
    }

    const caseId = url.searchParams.get('case');

    try {
      const results = await runFeedbackEvals(env, caseId);

      return Response.json(results);
    } catch (error) {
      return Response.json(
        {
          error: error.message
        },
        {
          status: 400
        }
      );
    }
  }
};