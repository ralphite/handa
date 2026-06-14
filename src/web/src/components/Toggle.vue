<script setup lang="ts">
defineOptions({
  name: 'Toggle',
})

const props = withDefaults(
  defineProps<{
    /** Whether the toggle is on. Use with v-model. */
    modelValue: boolean
    /** Disable interaction and dim the control. */
    disabled?: boolean
    /** Visual size of the control. */
    size?: 'sm' | 'md'
    /** Accessible name for standalone toggles that have no visible label. */
    ariaLabel?: string
  }>(),
  {
    disabled: false,
    size: 'md',
    ariaLabel: undefined,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  change: [value: boolean]
}>()

function onClick() {
  if (props.disabled) return
  const next = !props.modelValue
  emit('update:modelValue', next)
  emit('change', next)
}
</script>

<template>
  <button
    type="button"
    role="switch"
    class="toggle"
    :class="[`toggle--${size}`, { 'toggle--on': modelValue, 'toggle--disabled': disabled }]"
    :aria-checked="modelValue"
    :aria-label="ariaLabel"
    :disabled="disabled"
    @click="onClick"
  >
    <span class="toggle__knob" aria-hidden="true" />
  </button>
</template>

<style scoped>
.toggle {
  --toggle-w: 36px;
  --toggle-h: 20px;
  --toggle-pad: 2px;
  --toggle-knob: calc(var(--toggle-h) - var(--toggle-pad) * 2);

  position: relative;
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  box-sizing: border-box;
  width: var(--toggle-w);
  height: var(--toggle-h);
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: var(--border-muted);
  cursor: pointer;
  transition: background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  -webkit-tap-highlight-color: transparent;
}

.toggle--sm {
  --toggle-w: 28px;
  --toggle-h: 16px;
}

.toggle--on {
  background: var(--accent);
}

.toggle:hover:not(.toggle--disabled) {
  filter: brightness(1.06);
}

.toggle:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.toggle--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.toggle__knob {
  position: absolute;
  top: var(--toggle-pad);
  left: var(--toggle-pad);
  width: var(--toggle-knob);
  height: var(--toggle-knob);
  border-radius: 9999px;
  background: #fff;
  /* Shadow + hairline so the knob stays legible on light off-state tracks. */
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.28),
    0 0 0 0.5px rgba(0, 0, 0, 0.06);
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.toggle--on .toggle__knob {
  transform: translateX(calc(var(--toggle-w) - var(--toggle-knob) - var(--toggle-pad) * 2));
}
</style>
