export default {
  async fetch(request, env) {
    if (request.method !== 'POST') {
      return new Response('webhook-ok', { status: 200 });
    }

    const event = await request.json();

    // VK использует confirmation при подтверждении Callback URL.
    if (event.type === 'confirmation') {
      return new Response(env.VK_CONFIRMATION_CODE, {
        status: 200
      });
    }

    // Принимаем события только от нашего VK-сообщества.
    if (
      event.secret !== env.VK_CALLBACK_SECRET ||
      event.group_id !== 123456789
    ) {
      console.log('Rejected callback');

      return new Response('ok', { status: 200 });
    }

    if (event.type !== 'message_new') {
      console.log('Ignored event:', event.type);
      return new Response('ok', { status: 200 });
    }

    const message = event.object?.message;

    if (!message) {
      console.log('Ignored: message is missing');
      return new Response('ok', { status: 200 });
    }

    const peerId = String(message.peer_id);

    let environment;

    if (peerId === env.VK_TEST_PEER_ID) {
      environment = 'TEST';
    } else if (peerId === env.VK_PROD_PEER_ID) {
      environment = 'PROD';
    } else {
      console.log('Ignored unknown peer');
      return new Response('ok', { status: 200 });
    }

    console.log('Feedback received:', {
      environment,
      hasText: Boolean(message.text)
    });

    return new Response('ok', { status: 200 });
  }
};