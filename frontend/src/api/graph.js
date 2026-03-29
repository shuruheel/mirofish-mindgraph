import service, { requestWithRetry } from './index'

/**
 * Generate ontology (upload documents and simulation requirements)
 * @param {Object} data - Contains files, simulation_requirement, project_name, etc.
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return requestWithRetry(() => 
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  )
}

/**
 * Build graph
 * @param {Object} data - Contains project_id, graph_name, etc.
 * @returns {Promise}
 */
export function buildGraph(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/build',
      method: 'post',
      data
    })
  )
}

/**
 * Query task status
 * @param {String} taskId - Task ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * Get graph data
 * @param {String} graphId - Graph ID
 * @param {Object} params - Optional parameters { source: 'upload' | 'mindgraph' }
 * @returns {Promise}
 */
export function getGraphData(graphId, params = {}) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get',
    params
  })
}

/**
 * Get project information
 * @param {String} projectId - Project ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}

/**
 * Connect to an existing MindGraph knowledge graph (skip document upload and graph building)
 * @param {Object} data - { simulation_requirement?, project_name? }
 * @returns {Promise}
 */
export function connectMindGraph(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/connect',
      method: 'post',
      data
    })
  )
}
