import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createModels } from '@earendil-works/pi-ai/models';
import { InMemoryCredentialStore } from '@earendil-works/pi-ai';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { fauxProvider, fauxAssistantMessage, fauxToolCall } from '@earendil-works/pi-ai/providers/faux';

export async function createDemoModels(mode = process.env.PI_DEMO_MODE || 'live') {
  const credentials = new InMemoryCredentialStore();
  const models = createModels({ credentials });
  if (mode === 'scripted') {
    const faux = fauxProvider({ provider: 'demo', models: [{ id: 'scripted', name: 'Scripted test model' }] });
    const response = context => {
      const last = context.messages.at(-1);
      if (last?.role === 'toolResult') return fauxAssistantMessage('Summary saved. Ready for the next message.');
      const messages = context.messages.filter(m => m.role === 'user').map(m => {
        const text = typeof m.content === 'string' ? m.content : m.content.filter(b => b.type === 'text').map(b => b.text).join('');
        try { return JSON.parse(text); } catch { return { sender: 'Unknown', text }; }
      });
      const summary = 'Scripted summary — ' + messages.map(m => `${m.sender}: ${m.text}`).join('\n');
      return fauxAssistantMessage(fauxToolCall('record_summary', { summary, participants: [...new Set(messages.map(m => m.sender))] }), { stopReason: 'toolUse' });
    };
    faux.setResponses(Array(2000).fill(response));
    models.setProvider(faux.provider);
    return { models, provider: 'demo', modelId: 'scripted', auth: 'Scripted test model · no AI requests' };
  }
  if (mode !== 'live') throw new Error('PI_DEMO_MODE must be live or scripted.');
  if (process.env.OPENAI_API_KEY) {
    models.setProvider(openaiProvider());
    return { models, provider: 'openai', modelId: process.env.PI_DEMO_MODEL || 'gpt-6.1-sol', auth: 'OpenAI API key' };
  }
  const file = process.env.PI_DEMO_CODEX_AUTH || join(homedir(), '.codex', 'auth.json');
  const auth = JSON.parse(await readFile(file, 'utf8'));
  const access = auth.tokens?.access_token;
  if (!access) throw new Error('No Codex access token. Sign in using codex login, or set OPENAI_API_KEY.');
  const claims = JSON.parse(Buffer.from(access.split('.')[1], 'base64url').toString());
  const expires = claims.exp * 1000;
  if (!Number.isFinite(expires) || expires <= Date.now() + 300_000) {
    throw new Error('Codex access token has expired. Refresh your Codex login and restart the demo.');
  }
  const provider = openaiCodexProvider();
  // Never rotate the shared Codex refresh token or modify Codex's credential file.
  provider.auth.oauth.refresh = async () => {
    throw new Error('Refresh your Codex login and restart the demo.');
  };
  await credentials.modify('openai-codex', async () => ({
    type: 'oauth', access, refresh: '', expires,
    accountId: auth.tokens.account_id || claims['https://api.openai.com/auth']?.chatgpt_account_id,
  }));
  models.setProvider(provider);
  return { models, provider: 'openai-codex', modelId: process.env.PI_DEMO_MODEL || 'gpt-6.1-sol', auth: 'Existing Codex sign-in (read only)' };
}
