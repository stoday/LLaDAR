export async function ask(baseUrl: string, question: string, requestId: string): Promise<string> {
  const result = await fetch(`${baseUrl}/api/chat`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({request_id: requestId, input: {text: question}})
  });
  if (!result.ok) throw new Error('Chat request failed');
  return (await result.json()).data.answer;
}
