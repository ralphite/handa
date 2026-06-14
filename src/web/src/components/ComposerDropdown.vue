<script setup lang="ts">
import { Check, ChevronDown, Zap } from '@lucide/vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

defineOptions({
  name: 'ComposerDropdown',
})

interface ComposerDropdownOption {
  id: string
  label: string
  secondaryText?: string
  description?: string
  badge?: 'fast' | 'local' | null
  prefixKey?: string | null
}

const props = withDefaults(defineProps<{
  testId: string
  title: string
  tooltip?: string
  options: ComposerDropdownOption[]
  selectedId: string
  disabled?: boolean
  buttonClass?: string
  menuClass?: string
  selectedLabelTestId?: string
}>(), {
  disabled: false,
  buttonClass: 'max-w-[132px] sm:max-w-[240px]',
  menuClass: '',
})

const emit = defineEmits<{
  select: [id: string]
}>()

const rootRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
const menuPlacement = ref<'top' | 'bottom'>('top')
const menuOffsetX = ref(0)

const selectedOption = computed(() => {
  return props.options.find((option) => option.id === props.selectedId)
    ?? props.options[0]
    ?? null
})

const buttonTitle = computed(() => {
  if (props.tooltip) return props.tooltip
  if (!selectedOption.value) return props.title
  const label = selectedOption.value.secondaryText
    ? `${selectedOption.value.label} ${selectedOption.value.secondaryText}`
    : selectedOption.value.label
  return selectedOption.value.description
    ? `${label}: ${selectedOption.value.description}`
    : label
})

const buttonTooltip = computed(() => ({
  content: buttonTitle.value,
  disabled: menuOpen.value,
}))

const buttonClasses = computed(() => [
  'inline-flex h-8 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 text-[12px] font-medium text-[color:var(--text-muted)] transition',
  props.buttonClass,
  props.disabled
    ? 'cursor-not-allowed opacity-50'
    : 'hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)]',
])

const menuClasses = computed(() => [
  'absolute left-0 z-30 min-w-full w-max max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] py-1 shadow-2xl shadow-[var(--shadow-color)]',
  props.menuClass,
])

const menuStyle = computed(() => ({
  transform: menuOffsetX.value ? `translateX(${menuOffsetX.value}px)` : undefined,
}))

function updateMenuPlacement() {
  const rect = rootRef.value?.getBoundingClientRect()
  if (!rect) {
    menuPlacement.value = 'top'
    return
  }
  const estimatedMenuHeight = Math.min(props.options.length * 32 + 8, 320)
  const spaceAbove = rect.top
  const spaceBelow = window.innerHeight - rect.bottom
  menuPlacement.value = spaceAbove < estimatedMenuHeight && spaceBelow > spaceAbove
    ? 'bottom'
    : 'top'
}

function updateMenuHorizontalOffset() {
  const menu = menuRef.value
  if (!menu) return
  const viewportPadding = 16
  const rect = menu.getBoundingClientRect()
  const baseLeft = rect.left - menuOffsetX.value
  const baseRight = rect.right - menuOffsetX.value
  const maxRight = window.innerWidth - viewportPadding
  if (baseRight > maxRight) {
    menuOffsetX.value = maxRight - baseRight
  } else if (baseLeft < viewportPadding) {
    menuOffsetX.value = viewportPadding - baseLeft
  } else {
    menuOffsetX.value = 0
  }
}

function toggleMenu() {
  if (props.disabled || props.options.length === 0) return
  if (menuOpen.value) {
    menuOpen.value = false
    return
  }
  updateMenuPlacement()
  menuOffsetX.value = 0
  menuOpen.value = true
  nextTick(() => {
    window.requestAnimationFrame(updateMenuHorizontalOffset)
  })
}

function selectOption(id: string) {
  menuOpen.value = false
  emit('select', id)
}

function handleDocumentMouseDown(event: MouseEvent) {
  if (!menuOpen.value) return
  const target = event.target
  if (!(target instanceof Node)) return
  if (rootRef.value?.contains(target)) return
  menuOpen.value = false
}

watch(
  () => [props.disabled, props.options.length],
  () => {
    if (props.disabled || props.options.length === 0) {
      menuOpen.value = false
    }
  },
)

onMounted(() => {
  document.addEventListener('mousedown', handleDocumentMouseDown)
  window.addEventListener('resize', updateMenuHorizontalOffset)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleDocumentMouseDown)
  window.removeEventListener('resize', updateMenuHorizontalOffset)
})
</script>

<template>
  <div ref="rootRef" class="relative">
    <button
      :class="buttonClasses"
      type="button"
      v-tooltip="buttonTooltip"
      :aria-expanded="menuOpen"
      aria-haspopup="menu"
      :disabled="disabled"
      :data-testid="testId"
      @click="toggleMenu"
      @keydown.esc="menuOpen = false"
    >
      <slot name="button-prefix" :option="selectedOption" />
      <span
        class="min-w-0 truncate"
        :data-testid="selectedLabelTestId"
      >
        {{ selectedOption?.label ?? title }}
      </span>
      <span
        v-if="selectedOption?.secondaryText"
        class="hidden shrink-0 text-[color:var(--text-faint)] sm:inline"
      >
        {{ selectedOption.secondaryText }}
      </span>
      <ChevronDown :size="14" class="shrink-0" />
    </button>

    <div
      v-if="menuOpen"
      ref="menuRef"
      :class="[menuClasses, menuPlacement === 'top' ? 'bottom-full mb-2' : 'top-full mt-2']"
      :style="menuStyle"
      role="menu"
    >
      <button
        v-for="option in options"
        :key="option.id"
        class="composer-dropdown-row flex h-8 w-full items-center gap-2 px-3 text-left"
        :class="{ 'is-selected': option.id === selectedId }"
        type="button"
        role="menuitemradio"
        :aria-checked="option.id === selectedId"
        @click="selectOption(option.id)"
      >
        <span class="grid h-5 w-5 shrink-0 place-items-center text-[color:var(--accent)]">
          <Check v-if="option.id === selectedId" :size="16" stroke-width="2.3" />
        </span>
        <slot name="option-prefix" :option="option" />
        <span class="min-w-0 flex flex-1 items-baseline gap-1.5">
          <span class="truncate text-[12px] font-medium text-[color:var(--text-primary)]">{{ option.label }}</span>
          <span
            v-if="option.secondaryText"
            class="shrink-0 text-[11px] text-[color:var(--text-faint)]"
          >
            ({{ option.secondaryText }})
          </span>
        </span>
        <span
          v-if="option.badge === 'fast'"
          class="inline-flex h-5 shrink-0 items-center gap-1 rounded-full border border-transparent bg-[var(--accent-soft)] px-1.5 text-[10px] font-medium text-[color:var(--accent)]"
        >
          <Zap :size="12" stroke-width="2.4" />
          Fast
        </span>
        <span
          v-else-if="option.badge === 'local'"
          class="inline-flex h-5 shrink-0 items-center rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-muted)] px-1.5 text-[10px] font-medium text-[color:var(--text-muted)]"
        >
          Local
        </span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.composer-dropdown-row {
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.composer-dropdown-row:hover {
  background-color: color-mix(in srgb, var(--text-primary) 4%, transparent);
}

.composer-dropdown-row.is-selected {
  background-color: color-mix(in srgb, var(--text-primary) 5%, transparent);
}
</style>
