function resolveStorage(type) {
  try {
    if (typeof window === 'undefined') return null
    return window[type] || null
  } catch {
    return null
  }
}

export function readStorage(type, key, fallback = null) {
  try {
    return resolveStorage(type)?.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

export function writeStorage(type, key, value) {
  try {
    const storage = resolveStorage(type)
    if (!storage) return false
    storage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

export function removeStorage(type, key) {
  try {
    const storage = resolveStorage(type)
    if (!storage) return false
    storage.removeItem(key)
    return true
  } catch {
    return false
  }
}

export const readLocal = (key, fallback = null) => readStorage('localStorage', key, fallback)
export const writeLocal = (key, value) => writeStorage('localStorage', key, value)
export const removeLocal = (key) => removeStorage('localStorage', key)
export const readSession = (key, fallback = null) => readStorage('sessionStorage', key, fallback)
export const writeSession = (key, value) => writeStorage('sessionStorage', key, value)
export const removeSession = (key) => removeStorage('sessionStorage', key)
