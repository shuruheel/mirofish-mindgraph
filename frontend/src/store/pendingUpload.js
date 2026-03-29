/**
 * Temporary storage for pending file uploads and requirements
 * Used to redirect immediately after clicking "Launch Engine" on the Home page, then make API calls on the Process page
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  simulationRequirement: '',
  isPending: false,
  connectMode: false  // true = connect to existing MindGraph graph, skip document upload
})

export function setPendingUpload(files, requirement) {
  state.files = files
  state.simulationRequirement = requirement
  state.isPending = true
  state.connectMode = false
}

export function setPendingConnect(requirement) {
  state.files = []
  state.simulationRequirement = requirement
  state.isPending = true
  state.connectMode = true
}

export function getPendingUpload() {
  return {
    files: state.files,
    simulationRequirement: state.simulationRequirement,
    isPending: state.isPending,
    connectMode: state.connectMode
  }
}

export function clearPendingUpload() {
  state.files = []
  state.simulationRequirement = ''
  state.isPending = false
  state.connectMode = false
}

export default state
