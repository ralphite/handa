import { computed, ref } from 'vue'
import { dictate } from '../api/client'

export type DictationState = 'idle' | 'recording' | 'transcribing'

/**
 * Wraps MediaRecorder + the /api/dictate endpoint.
 *
 * Lifecycle: idle -> recording -> transcribing -> idle.
 * The caller decides what to do with the resulting transcript (typically
 * append it to the composer draft).
 */
export function useDictation(options: {
  getSessionId: () => string | undefined
  getProjectId: () => string | undefined
  onTranscript: (text: string) => void
  onError?: (message: string) => void
}) {
  const state = ref<DictationState>('idle')
  const errorMessage = ref<string>('')

  let mediaRecorder: MediaRecorder | null = null
  let mediaStream: MediaStream | null = null
  let chunks: Blob[] = []
  // True when the user actively asked to abort — we still receive a stop
  // event from the recorder but must not transcribe.
  let aborted = false

  const isRecording = computed(() => state.value === 'recording')
  const isTranscribing = computed(() => state.value === 'transcribing')
  const isBusy = computed(() => state.value !== 'idle')

  function pickMimeType(): string {
    // Preference order: webm/opus is the most broadly supported on Chromium;
    // mp4/m4a is Safari's only format. Gemini accepts both.
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
      'audio/mp4;codecs=mp4a.40.2',
      'audio/aac',
    ]
    if (typeof MediaRecorder === 'undefined') return ''
    for (const candidate of candidates) {
      if (MediaRecorder.isTypeSupported(candidate)) return candidate
    }
    return ''
  }

  function cleanup() {
    mediaStream?.getTracks().forEach((track) => track.stop())
    mediaStream = null
    mediaRecorder = null
    chunks = []
  }

  function fail(message: string) {
    errorMessage.value = message
    options.onError?.(message)
    state.value = 'idle'
    cleanup()
  }

  async function start() {
    if (state.value !== 'idle') return
    errorMessage.value = ''
    aborted = false

    if (!navigator.mediaDevices?.getUserMedia) {
      fail('Microphone is not available in this browser')
      return
    }

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (exc) {
      fail(exc instanceof Error ? exc.message : 'Failed to access microphone')
      return
    }

    const mimeType = pickMimeType()
    let recorder: MediaRecorder
    try {
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
    } catch (exc) {
      stream.getTracks().forEach((track) => track.stop())
      fail(exc instanceof Error ? exc.message : 'MediaRecorder unavailable')
      return
    }

    mediaStream = stream
    mediaRecorder = recorder
    chunks = []

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) chunks.push(event.data)
    }

    recorder.onstop = async () => {
      const recordedChunks = chunks
      const recordedType = recorder.mimeType || mimeType || 'audio/webm'
      cleanup()

      if (aborted) {
        state.value = 'idle'
        return
      }
      if (!recordedChunks.length) {
        state.value = 'idle'
        return
      }

      state.value = 'transcribing'
      const blob = new Blob(recordedChunks, { type: recordedType })
      try {
        const { transcript } = await dictate(blob, {
          sessionId: options.getSessionId(),
          projectId: options.getProjectId(),
        })
        if (transcript) options.onTranscript(transcript)
        state.value = 'idle'
      } catch (exc) {
        fail(exc instanceof Error ? exc.message : 'Transcription failed')
      }
    }

    recorder.onerror = (event) => {
      const err = (event as unknown as { error?: { message?: string } }).error
      fail(err?.message || 'Microphone error')
    }

    try {
      recorder.start()
      state.value = 'recording'
    } catch (exc) {
      fail(exc instanceof Error ? exc.message : 'Failed to start recording')
    }
  }

  function stop() {
    if (state.value !== 'recording' || !mediaRecorder) return
    try {
      mediaRecorder.stop()
    } catch (exc) {
      fail(exc instanceof Error ? exc.message : 'Failed to stop recording')
    }
  }

  function cancel() {
    if (state.value !== 'recording' || !mediaRecorder) return
    aborted = true
    try {
      mediaRecorder.stop()
    } catch {
      // Already stopped — fall through to manual cleanup.
      cleanup()
      state.value = 'idle'
    }
  }

  function toggle() {
    if (state.value === 'recording') stop()
    else if (state.value === 'idle') void start()
  }

  return {
    state,
    isRecording,
    isTranscribing,
    isBusy,
    errorMessage,
    start,
    stop,
    cancel,
    toggle,
  }
}
