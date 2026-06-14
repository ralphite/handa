<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Check, CornerDownLeft, Loader2, X } from '@lucide/vue'
import type { PendingUserInputRequest, UserInputAnswer, UserInputQuestion } from '../types'

defineOptions({
  name: 'UserInputForm',
})

const props = defineProps<{
  request: PendingUserInputRequest
  submitting?: boolean
}>()

const emit = defineEmits<{
  submit: [payload: { requestId: string; turnId: string; answers: UserInputAnswer[] }]
  cancel: [payload: { requestId: string; turnId: string }]
}>()

const selectedByQuestion = reactive<Record<string, string[]>>({})
const freeTextByQuestion = reactive<Record<string, string>>({})

// Questions are shown one step at a time; answers for every step are still
// submitted together in a single payload once the last step completes.
const currentStepIndex = ref(0)
const freeTextInput = ref<HTMLInputElement | null>(null)

const questions = computed(() => props.request.questions)
const totalSteps = computed(() => questions.value.length)
const currentQuestion = computed(() => questions.value[currentStepIndex.value])
const isLastStep = computed(() => currentStepIndex.value >= totalSteps.value - 1)

watch(
  () => props.request.requestId,
  () => {
    currentStepIndex.value = 0
    for (const key of Object.keys(selectedByQuestion)) delete selectedByQuestion[key]
    for (const key of Object.keys(freeTextByQuestion)) delete freeTextByQuestion[key]
  },
)

function selectedFor(question: UserInputQuestion): string[] {
  return selectedByQuestion[question.id] ?? []
}

function isSelected(question: UserInputQuestion, label: string) {
  return selectedFor(question).includes(label)
}

function isAnswered(question: UserInputQuestion) {
  return selectedFor(question).length > 0 || Boolean((freeTextByQuestion[question.id] ?? '').trim())
}

const currentAnswered = computed(() =>
  currentQuestion.value ? isAnswered(currentQuestion.value) : false,
)

const canAdvance = computed(() => !props.submitting && currentAnswered.value)

const canSubmit = computed(
  () => !props.submitting && questions.value.every(isAnswered),
)

const hasFreeTextDraft = computed(() =>
  questions.value.some((question) => (freeTextByQuestion[question.id] ?? '').trim()),
)

// Single single-select question: picking an option is the whole answer, so it
// submits right away and the primary button only appears for free-text drafts.
const singleAutoSubmits = computed(
  () => totalSteps.value === 1 && questions.value[0]?.multiSelect === false,
)

const showPrimaryButton = computed(() => !singleAutoSubmits.value || hasFreeTextDraft.value)

const primaryDisabled = computed(() => (isLastStep.value ? !canSubmit.value : !canAdvance.value))

function toggleOption(question: UserInputQuestion, label: string) {
  if (props.submitting) return
  const current = selectedFor(question)
  if (question.multiSelect) {
    selectedByQuestion[question.id] = current.includes(label)
      ? current.filter((item) => item !== label)
      : [...current, label]
    return
  }
  const deselecting = current.includes(label)
  selectedByQuestion[question.id] = deselecting ? [] : [label]
  // Picking a single-select option is a complete answer: move on immediately
  // (submitting when this is the last step).
  if (!deselecting) advance()
}

function advance() {
  if (!canAdvance.value) return
  if (isLastStep.value) {
    submitAnswers()
    return
  }
  currentStepIndex.value += 1
}

function goBack() {
  if (props.submitting) return
  if (currentStepIndex.value > 0) currentStepIndex.value -= 1
}

function focusFreeText() {
  freeTextInput.value?.focus()
}

function submitAnswers() {
  if (!canSubmit.value) return
  const answers: UserInputAnswer[] = questions.value.map((question) => {
    const freeText = (freeTextByQuestion[question.id] ?? '').trim()
    const answer: UserInputAnswer = {
      id: question.id,
      selected: selectedFor(question),
    }
    if (freeText) answer.free_text = freeText
    return answer
  })
  emit('submit', {
    requestId: props.request.requestId,
    turnId: props.request.turnId,
    answers,
  })
}

function cancelForm() {
  if (props.submitting) return
  emit('cancel', {
    requestId: props.request.requestId,
    turnId: props.request.turnId,
  })
}

// Number keys pick options and Enter advances, but only while the user is not
// typing somewhere else (composer, free-text input, ...).
function onWindowKeydown(event: KeyboardEvent) {
  if (props.submitting) return
  const target = event.target as HTMLElement | null
  if (
    target &&
    (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
  ) {
    return
  }
  const question = currentQuestion.value
  if (!question) return
  if (event.key === 'Enter') {
    if (!canAdvance.value) return
    event.preventDefault()
    advance()
    return
  }
  if (!/^[1-9]$/.test(event.key)) return
  const optionIndex = Number(event.key) - 1
  if (optionIndex < question.options.length) {
    event.preventDefault()
    toggleOption(question, question.options[optionIndex]!.label)
  } else if (question.allowFreeText && optionIndex === question.options.length) {
    event.preventDefault()
    focusFreeText()
  }
}

onMounted(() => window.addEventListener('keydown', onWindowKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onWindowKeydown))
</script>

<template>
  <section
    class="user-input-form mt-3 rounded-xl border p-4"
    data-testid="user-input-form"
    aria-label="Agent question form"
  >
    <header class="mb-3 flex items-start gap-2.5">
      <span
        v-if="totalSteps > 1"
        class="user-input-step-badge mt-[2px] shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-semibold leading-4"
        data-testid="user-input-step-indicator"
      >{{ currentStepIndex + 1 }}/{{ totalSteps }}</span>
      <span
        v-if="currentQuestion"
        class="min-w-0 flex-1 text-[14px] font-medium leading-6 text-[color:var(--text-primary)]"
      >
        {{ currentQuestion.prompt }}
        <span
          v-if="currentQuestion.multiSelect"
          class="ml-1 text-[12px] font-normal text-[color:var(--text-muted)]"
        >(multiple choice)</span>
      </span>
      <button
        class="user-input-close -mr-1 -mt-1 shrink-0 rounded-md p-1 text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
        type="button"
        :disabled="submitting"
        data-testid="user-input-close"
        aria-label="Dismiss"
        @click="cancelForm"
      >
        <X :size="15" />
      </button>
    </header>

    <!-- No <Transition> here: it depends on requestAnimationFrame, which stalls
         in hidden tabs and leaves the previous step stuck on screen. -->
    <fieldset
      v-if="currentQuestion"
      :key="currentQuestion.id"
      class="min-w-0"
      :disabled="submitting"
      :aria-label="currentQuestion.prompt"
    >
      <div class="flex flex-col gap-1.5">
        <button
          v-for="(option, optionIndex) in currentQuestion.options"
          :key="option.label"
          class="user-input-option flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
          :class="{ 'user-input-option-selected': isSelected(currentQuestion, option.label) }"
          type="button"
          :data-testid="`user-input-option-${currentQuestion.id}-${option.label}`"
          :aria-pressed="isSelected(currentQuestion, option.label)"
          @click="toggleOption(currentQuestion, option.label)"
        >
          <span
            v-if="currentQuestion.multiSelect"
            class="user-input-indicator mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded-[5px] border"
          >
            <Check
              v-if="isSelected(currentQuestion, option.label)"
              :size="11"
              stroke-width="3"
            />
          </span>
          <span class="min-w-0 flex-1">
            <span class="block text-[13px] font-medium text-[color:var(--text-primary)]">{{ option.label }}</span>
            <span
              v-if="option.description"
              class="block text-[12px] leading-5 text-[color:var(--text-muted)]"
            >{{ option.description }}</span>
          </span>
          <kbd class="user-input-kbd mt-[1px] shrink-0">{{ optionIndex + 1 }}</kbd>
        </button>

        <div
          v-if="currentQuestion.allowFreeText && currentQuestion.options.length"
          class="user-input-option user-input-other grid w-full cursor-text grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2.5 gap-y-1.5 rounded-lg border px-3 py-2"
          :class="{ 'user-input-option-selected': Boolean((freeTextByQuestion[currentQuestion.id] ?? '').trim()) }"
          @click="focusFreeText"
        >
          <span class="min-w-0 text-[13px] font-medium text-[color:var(--text-primary)]">Other</span>
          <kbd class="user-input-kbd mt-[1px] shrink-0">{{ currentQuestion.options.length + 1 }}</kbd>
          <input
            ref="freeTextInput"
            v-model="freeTextByQuestion[currentQuestion.id]"
            class="user-input-free-text col-span-2 w-full rounded-lg border bg-transparent px-3 py-2 text-[13px] text-[color:var(--text-primary)] outline-none placeholder:text-[color:var(--text-faint)] focus:border-[color:var(--border-strong,var(--border-subtle))]"
            type="text"
            placeholder="Type your own answer here"
            :data-testid="`user-input-free-text-${currentQuestion.id}`"
            @keydown.enter.prevent="advance()"
          />
        </div>
        <input
          v-else-if="currentQuestion.allowFreeText"
          ref="freeTextInput"
          v-model="freeTextByQuestion[currentQuestion.id]"
          class="user-input-free-text mt-0.5 w-full rounded-lg border bg-transparent px-3 py-2 text-[13px] text-[color:var(--text-primary)] outline-none placeholder:text-[color:var(--text-faint)] focus:border-[color:var(--border-strong,var(--border-subtle))]"
          type="text"
          placeholder="Type your answer"
          :data-testid="`user-input-free-text-${currentQuestion.id}`"
          @keydown.enter.prevent="advance()"
        />
      </div>
    </fieldset>

    <footer class="mt-4 flex items-center gap-2">
      <button
        v-if="currentStepIndex > 0"
        class="rounded-md border border-[color:var(--border-muted)] px-3 py-1.5 text-[12px] font-medium text-[color:var(--text-primary)] transition hover:bg-[var(--surface-hover)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
        type="button"
        :disabled="submitting"
        data-testid="user-input-back"
        @click="goBack"
      >
        Back
      </button>
      <span class="flex-1" />
      <button
        class="rounded-md px-3 py-1.5 text-[12px] font-medium text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
        type="button"
        :disabled="submitting"
        data-testid="user-input-skip"
        @click="cancelForm"
      >
        Skip
      </button>
      <button
        v-if="showPrimaryButton"
        class="user-input-submit inline-flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-[12px] font-medium transition focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)] disabled:cursor-not-allowed disabled:opacity-50"
        type="button"
        :disabled="primaryDisabled"
        :data-testid="isLastStep ? 'user-input-submit' : 'user-input-next'"
        @click="advance"
      >
        <Loader2 v-if="submitting" :size="13" class="animate-spin" />
        {{ isLastStep ? 'Submit' : 'Next' }}
        <CornerDownLeft v-if="!submitting" :size="12" class="opacity-70" />
      </button>
    </footer>
  </section>
</template>

<style scoped>
.user-input-form {
  background: var(--panel-bg);
  border-color: var(--border-subtle);
}

.user-input-step-badge {
  background: var(--accent-soft, var(--surface-active));
  color: var(--accent, var(--text-primary));
}

.user-input-option {
  background: transparent;
  border-color: var(--border-muted);
}

.user-input-option:hover {
  background: var(--surface-hover);
}

.user-input-option-selected {
  background: var(--accent-soft, var(--surface-active));
  border-color: var(--accent, var(--border-subtle));
}

.user-input-indicator {
  border-color: var(--border-subtle);
  color: transparent;
}

.user-input-option-selected .user-input-indicator {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-contrast, #fff);
}

.user-input-kbd {
  display: grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border: 1px solid var(--border-muted);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  line-height: 1;
  color: var(--text-muted);
  background: var(--surface-hover, transparent);
}

.user-input-other:hover {
  background: transparent;
}

.user-input-free-text {
  border-color: var(--border-muted);
}

.user-input-submit {
  background: var(--accent);
  color: var(--accent-contrast, #fff);
}

.user-input-submit:not(:disabled):hover {
  filter: brightness(1.08);
}
</style>
