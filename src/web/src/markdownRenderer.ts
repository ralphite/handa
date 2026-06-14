import { enableKatex, setCustomComponents } from 'markstream-vue'
import HighlightCodeBlock from './components/HighlightCodeBlock.vue'

export function configureMarkdownRenderer() {
  enableKatex()
  setCustomComponents({
    code_block: HighlightCodeBlock,
  })
}
