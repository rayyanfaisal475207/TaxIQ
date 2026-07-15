// ============================================================
// API Layer — Axios Client & SSE Streams
// ============================================================

import axios, { AxiosError } from 'axios';
import type { Attachment, PipelineEvent } from '../types';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

// ── Axios Instance ──────────────────────────────────────────────────────────
export const apiClient = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
});

// Request Interceptor: Attach CSRF Token
apiClient.interceptors.request.use((config) => {
  if (config.method && ['post', 'put', 'delete', 'patch'].includes(config.method.toLowerCase())) {
    const match = document.cookie.match(new RegExp('(^| )csrf_token=([^;]+)'));
    if (match) {
      config.headers['X-CSRF-Token'] = match[2];
    }
  }
  return config;
});

// Response Interceptor: Global 401 handler
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response && error.response.status === 401) {
      // Dispatch custom event caught by App.tsx or authStore
      window.dispatchEvent(new Event('auth:unauthorized'));
    }
    return Promise.reject(error);
  }
);

// ── SSE Streaming Chat ──────────────────────────────────────────────────────
export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (event: PipelineEvent) => void,
  signal?: AbortSignal,
  projectId?: string | null,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  
  const csrfMatch = document.cookie.match(new RegExp('(^| )csrf_token=([^;]+)'));
  if (csrfMatch) {
    headers['X-CSRF-Token'] = csrfMatch[2];
  }

  const bodyData: any = { session_id: sessionId, message };
  if (projectId) {
    bodyData.project_id = projectId;
  }

  const response = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify(bodyData),
    credentials: 'include', // Important for HttpOnly cookie
    signal,
  });

  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('auth:unauthorized'));
    const text = await response.text();
    throw new Error(`Chat request failed: ${response.status} ${text}`);
  }

  if (!response.body) throw new Error('No response body received');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data: ')) continue;
      try {
        const event = JSON.parse(line.slice(6)) as PipelineEvent;
        onEvent(event);
      } catch {
        // silently ignore malformed JSON
      }
    }
  }

  if (buffer.trim().startsWith('data: ')) {
    try {
      const event = JSON.parse(buffer.trim().slice(6)) as PipelineEvent;
      onEvent(event);
    } catch {
      // ignore
    }
  }
}

// ── Chat attachments ────────────────────────────────────────────────────────
// Files attached to ONE conversation. These are NOT knowledge-base documents:
// they are never embedded or indexed, and are visible only to this session.
// Knowledge-base ingestion lives in the admin app.

export async function uploadAttachment(sessionId: string, file: File): Promise<Attachment> {
  const form = new FormData();
  form.append('session_id', sessionId);
  form.append('file', file);
  const { data } = await apiClient.post<Attachment>('/attachments', form);
  return data;
}

export async function listAttachments(sessionId: string): Promise<Attachment[]> {
  const { data } = await apiClient.get<Attachment[]>('/attachments', {
    params: { session_id: sessionId },
  });
  return data;
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  await apiClient.delete(`/attachments/${attachmentId}`);
}

// ── Health ──────────────────────────────────────────────────────────────────
export async function getHealth(): Promise<Record<string, unknown>> {
  const { data } = await apiClient.get<Record<string, unknown>>('/health');
  return data;
}

