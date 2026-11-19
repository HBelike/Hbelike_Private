// 浏览器只在已有授权或用户主动启用时读取坐标，不持久化经纬度。
const ATTRIBUTION = 'BigDataCloud'
const LOCATION_ENDPOINT = 'https://api.bigdatacloud.net/data/reverse-geocode-client'

function result(status, message, city = null) {
  return { status, message, city, attribution: ATTRIBUTION, attributionUrl: 'https://www.bigdatacloud.com/' }
}

function normalizeCityName(value) {
  return String(value ?? '').trim().replace(/(特别行政区|自治州|地区|市|盟)$/, '')
}

export function matchLocatedCity(name, cities = []) {
  const target = normalizeCityName(name)
  if (!target) return null
  return cities.find((city) => normalizeCityName(city.name) === target) ?? null
}

export async function resolveJobSearchCity({
  cities = [],
  requestPermission = false,
  navigator: browserNavigator = globalThis.navigator,
  fetch: fetchLocation = globalThis.fetch
} = {}) {
  if (!browserNavigator?.geolocation) {
    return result('unavailable', '当前浏览器无法定位，请手动选择工作城市。')
  }
  if (!requestPermission) {
    try {
      const permission = await browserNavigator.permissions?.query({ name: 'geolocation' })
      if (permission?.state === 'denied') {
        return result('denied', '位置权限已关闭，请在浏览器中允许定位，或手动选择城市。')
      }
      if (permission?.state !== 'granted') {
        return result('permission_required', '尚未获取当前位置，可以启用定位服务，或手动选择工作城市。')
      }
    } catch {
      return result('permission_required', '需要你启用定位服务，也可以手动选择工作城市。')
    }
  }
  let position
  try {
    position = await new Promise((resolve, reject) => {
      browserNavigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: false,
        timeout: 8000,
        maximumAge: 0
      })
    })
  } catch (error) {
    return error?.code === 1
      ? result('denied', '未获得位置权限，请在浏览器中允许定位，或手动选择城市。')
      : result('unavailable', '暂时无法获取当前位置，可以重试定位，或手动选择城市。')
  }
  const latitude = Number(position?.coords?.latitude)
  const longitude = Number(position?.coords?.longitude)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)
    || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
    return result('unavailable', '未获得有效位置，请手动选择工作城市。')
  }
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    // 免费端点仅允许设备直接传送本次经授权获得的当前位置，不经服务端代理。
    const query = new URLSearchParams({ latitude: String(latitude), longitude: String(longitude), localityLanguage: 'zh' })
    const response = await fetchLocation(`${LOCATION_ENDPOINT}?${query}`, {
      credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal
    })
    if (!response.ok) throw new Error('位置查询失败')
    const payload = await response.json()
    const city = matchLocatedCity(payload.city, cities)
    if (!city) return result('unavailable', '当前位置暂未匹配到招聘城市，请手动选择城市。')
    return result('located', `已定位到${city.name}，你可以修改工作城市。`, city)
  } catch {
    return result('unavailable', '暂时无法识别当前位置的城市，请重试定位或手动选择城市。')
  } finally {
    clearTimeout(timeout)
  }
}
