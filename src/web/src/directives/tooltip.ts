import type { Directive, DirectiveBinding } from 'vue'

type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right'

interface TooltipOptions {
  content?: string | number | null
  placement?: TooltipPlacement
  delay?: number
  disabled?: boolean
  overflowOnly?: boolean
}

type TooltipValue = string | number | null | undefined | false | TooltipOptions

interface TooltipState {
  content: string
  delay: number
  disabled: boolean
  overflowOnly: boolean
  placement: TooltipPlacement
  showTimer: number | null
  hideTimer: number | null
  cleanup: () => void
}

const TOOLTIP_ID = 'handa-global-tooltip'
const DEFAULT_DELAY = 600
const WARM_DELAY = 80
const WARM_WINDOW = 1000
const HIDE_DELAY = 40
const OFFSET = 9
const VIEWPORT_PADDING = 8
const OVERFLOW_EPSILON = 1

const states = new WeakMap<HTMLElement, TooltipState>()
let activeElement: HTMLElement | null = null
let activeState: TooltipState | null = null
let tooltipElement: HTMLDivElement | null = null
let tooltipContentElement: HTMLDivElement | null = null
let globalHideTimer: number | null = null
let lastShownAt: number | null = null

function resolveTooltipOptions(value: TooltipValue): Omit<TooltipState, 'showTimer' | 'hideTimer' | 'cleanup'> {
  if (typeof value === 'object' && value !== null) {
    return {
      content: stringifyContent(value.content),
      delay: value.delay ?? DEFAULT_DELAY,
      disabled: Boolean(value.disabled),
      overflowOnly: Boolean(value.overflowOnly),
      placement: value.placement ?? 'top',
    }
  }

  return {
    content: stringifyContent(value),
    delay: DEFAULT_DELAY,
    disabled: value === false,
    overflowOnly: false,
    placement: 'top',
  }
}

function stringifyContent(value: string | number | null | undefined | false) {
  if (value === null || value === undefined || value === false) return ''
  return String(value).trim()
}

function ensureTooltipElement() {
  if (tooltipElement && tooltipContentElement) {
    return { tooltip: tooltipElement, content: tooltipContentElement }
  }

  const tooltip = document.createElement('div')
  tooltip.id = TOOLTIP_ID
  tooltip.className = 'handa-tooltip'
  tooltip.role = 'tooltip'
  tooltip.hidden = true

  const content = document.createElement('div')
  content.className = 'handa-tooltip__content'
  tooltip.appendChild(content)

  const arrow = document.createElement('div')
  arrow.className = 'handa-tooltip__arrow'
  tooltip.appendChild(arrow)

  document.body.appendChild(tooltip)
  tooltipElement = tooltip
  tooltipContentElement = content
  return { tooltip, content }
}

function clearTimer(timer: number | null) {
  if (timer !== null) window.clearTimeout(timer)
}

function clamp(value: number, min: number, max: number) {
  const safeMax = Math.max(min, max)
  return Math.min(Math.max(value, min), safeMax)
}

function pickPlacement(preferred: TooltipPlacement, triggerRect: DOMRect, tooltipRect: DOMRect) {
  const space = {
    top: triggerRect.top,
    bottom: window.innerHeight - triggerRect.bottom,
    left: triggerRect.left,
    right: window.innerWidth - triggerRect.right,
  }

  if (preferred === 'top' && space.top < tooltipRect.height + OFFSET && space.bottom > space.top) return 'bottom'
  if (preferred === 'bottom' && space.bottom < tooltipRect.height + OFFSET && space.top > space.bottom) return 'top'
  if (preferred === 'left' && space.left < tooltipRect.width + OFFSET && space.right > space.left) return 'right'
  if (preferred === 'right' && space.right < tooltipRect.width + OFFSET && space.left > space.right) return 'left'
  return preferred
}

function positionTooltip(element: HTMLElement, state: TooltipState) {
  if (!tooltipElement) return

  const triggerRect = element.getBoundingClientRect()
  const tooltipRect = tooltipElement.getBoundingClientRect()
  const placement = pickPlacement(state.placement, triggerRect, tooltipRect)
  const triggerCenterX = triggerRect.left + triggerRect.width / 2
  const triggerCenterY = triggerRect.top + triggerRect.height / 2
  let top = 0
  let left = 0

  if (placement === 'top' || placement === 'bottom') {
    left = clamp(
      triggerCenterX - tooltipRect.width / 2,
      VIEWPORT_PADDING,
      window.innerWidth - VIEWPORT_PADDING - tooltipRect.width,
    )
    top = placement === 'top'
      ? triggerRect.top - tooltipRect.height - OFFSET
      : triggerRect.bottom + OFFSET
  } else {
    top = clamp(
      triggerCenterY - tooltipRect.height / 2,
      VIEWPORT_PADDING,
      window.innerHeight - VIEWPORT_PADDING - tooltipRect.height,
    )
    left = placement === 'left'
      ? triggerRect.left - tooltipRect.width - OFFSET
      : triggerRect.right + OFFSET
  }

  left = clamp(left, VIEWPORT_PADDING, window.innerWidth - VIEWPORT_PADDING - tooltipRect.width)
  top = clamp(top, VIEWPORT_PADDING, window.innerHeight - VIEWPORT_PADDING - tooltipRect.height)
  if (placement === 'top' || placement === 'bottom') {
    tooltipElement.style.setProperty('--tooltip-arrow-x', `${clamp(triggerCenterX - left, 12, tooltipRect.width - 12)}px`)
  } else {
    tooltipElement.style.setProperty('--tooltip-arrow-y', `${clamp(triggerCenterY - top, 12, tooltipRect.height - 12)}px`)
  }
  tooltipElement.dataset.placement = placement
  tooltipElement.style.left = `${Math.round(left)}px`
  tooltipElement.style.top = `${Math.round(top)}px`
}

function updateActivePosition() {
  if (!activeElement || !activeState) return
  positionTooltip(activeElement, activeState)
}

function addGlobalPositionListeners() {
  window.addEventListener('resize', updateActivePosition)
  window.addEventListener('scroll', updateActivePosition, true)
}

function removeGlobalPositionListeners() {
  window.removeEventListener('resize', updateActivePosition)
  window.removeEventListener('scroll', updateActivePosition, true)
}

function attachDescription(element: HTMLElement) {
  const ids = new Set((element.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean))
  ids.add(TOOLTIP_ID)
  element.setAttribute('aria-describedby', Array.from(ids).join(' '))
}

function detachDescription(element: HTMLElement) {
  const ids = (element.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
  const nextIds = ids.filter((id) => id !== TOOLTIP_ID)
  if (nextIds.length) {
    element.setAttribute('aria-describedby', nextIds.join(' '))
  } else {
    element.removeAttribute('aria-describedby')
  }
}

function showTooltip(element: HTMLElement, state: TooltipState) {
  clearTimer(globalHideTimer)
  globalHideTimer = null

  if (state.disabled || !state.content || (state.overflowOnly && !isElementOverflowing(element))) {
    hideTooltip(element, true)
    return
  }

  const { tooltip, content } = ensureTooltipElement()
  if (activeElement && activeElement !== element) detachDescription(activeElement)
  content.textContent = state.content
  tooltip.hidden = false
  tooltip.classList.remove('is-visible')
  tooltip.dataset.placement = state.placement

  activeElement = element
  activeState = state
  attachDescription(element)
  positionTooltip(element, state)
  window.requestAnimationFrame(() => {
    if (activeElement === element) tooltip.classList.add('is-visible')
  })
  lastShownAt = Date.now()
  addGlobalPositionListeners()
}

function hideTooltip(element: HTMLElement, immediate = false) {
  clearTimer(globalHideTimer)
  detachDescription(element)

  if (activeElement === element) {
    activeElement = null
    activeState = null
    removeGlobalPositionListeners()
    tooltipElement?.classList.remove('is-visible')
  }

  if (immediate) {
    if (tooltipElement) tooltipElement.hidden = true
    return
  }

  globalHideTimer = window.setTimeout(() => {
    if (!activeElement && tooltipElement) tooltipElement.hidden = true
  }, 90)
}

function scheduleShow(element: HTMLElement, state: TooltipState) {
  clearTimer(state.hideTimer)
  clearTimer(state.showTimer)
  const delay = isWarmTooltipSession() ? Math.min(state.delay, WARM_DELAY) : state.delay
  state.showTimer = window.setTimeout(() => {
    showTooltip(element, state)
  }, delay)
}

function isWarmTooltipSession() {
  return Boolean(activeElement) || (lastShownAt !== null && Date.now() - lastShownAt <= WARM_WINDOW)
}

function scheduleHide(element: HTMLElement, state: TooltipState) {
  clearTimer(state.showTimer)
  clearTimer(state.hideTimer)
  state.hideTimer = window.setTimeout(() => {
    hideTooltip(element)
  }, HIDE_DELAY)
}

function updateState(element: HTMLElement, binding: DirectiveBinding<TooltipValue>) {
  const state = states.get(element)
  if (!state) return

  const nativeTitle = element.getAttribute('title')
  const next = resolveTooltipOptions(binding.value ?? nativeTitle)
  state.content = next.content
  state.delay = next.delay
  state.disabled = next.disabled
  state.overflowOnly = next.overflowOnly
  state.placement = next.placement
  if (nativeTitle !== null) element.removeAttribute('title')

  if (activeElement !== element) return
  if (state.disabled || !state.content || (state.overflowOnly && !isElementOverflowing(element))) {
    hideTooltip(element, true)
    return
  }

  if (tooltipContentElement) tooltipContentElement.textContent = state.content
  positionTooltip(element, state)
}

function bindTooltip(element: HTMLElement, binding: DirectiveBinding<TooltipValue>) {
  const next = resolveTooltipOptions(binding.value ?? element.getAttribute('title'))
  const onPointerEnter = (event: PointerEvent) => {
    if (event.pointerType === 'touch') return
    const state = states.get(element)
    if (state) scheduleShow(element, state)
  }
  const onPointerLeave = () => {
    const state = states.get(element)
    if (state) scheduleHide(element, state)
  }
  const onFocusIn = () => {
    const state = states.get(element)
    if (state) scheduleShow(element, state)
  }
  const onFocusOut = () => {
    const state = states.get(element)
    if (state) scheduleHide(element, state)
  }
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return
    const state = states.get(element)
    if (!state) return
    clearTimer(state.showTimer)
    hideTooltip(element, true)
  }

  const cleanup = () => {
    element.removeEventListener('pointerenter', onPointerEnter)
    element.removeEventListener('pointerleave', onPointerLeave)
    element.removeEventListener('focusin', onFocusIn)
    element.removeEventListener('focusout', onFocusOut)
    element.removeEventListener('keydown', onKeyDown)
  }

  states.set(element, {
    ...next,
    showTimer: null,
    hideTimer: null,
    cleanup,
  })

  if (element.hasAttribute('title')) element.removeAttribute('title')
  element.addEventListener('pointerenter', onPointerEnter)
  element.addEventListener('pointerleave', onPointerLeave)
  element.addEventListener('focusin', onFocusIn)
  element.addEventListener('focusout', onFocusOut)
  element.addEventListener('keydown', onKeyDown)
}

function isElementOverflowing(element: HTMLElement) {
  return (
    element.scrollWidth > element.clientWidth + OVERFLOW_EPSILON
    || element.scrollHeight > element.clientHeight + OVERFLOW_EPSILON
  )
}

export const tooltipDirective: Directive<HTMLElement, TooltipValue> = {
  mounted(element, binding) {
    bindTooltip(element, binding)
  },
  updated(element, binding) {
    updateState(element, binding)
  },
  beforeUnmount(element) {
    const state = states.get(element)
    if (!state) return
    clearTimer(state.showTimer)
    clearTimer(state.hideTimer)
    state.cleanup()
    hideTooltip(element, true)
    states.delete(element)
  },
}
