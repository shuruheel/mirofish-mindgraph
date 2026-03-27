/**
 * 临时存储待上传的文件和需求
 * 用于首页点击启动引擎后立即跳转，在Process页面再进行API调用
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  simulationRequirement: '',
  isPending: false,
  connectMode: false  // true = 连接已有MindGraph图谱，跳过文档上传
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
