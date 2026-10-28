import { parameters, syncTool, textParameter } from './definition.js'
import { fetchJson } from './network.js'
import { requireValue } from '../core/support.js'

/**
 * 查询指定城市或后端网络出口位置的当前天气；IP 位置仅短期缓存。
 * @param {import('../settings/interface.js').Settings} settings 全局配置。
 * @param {typeof fetch} transport 网络传输。
 * @returns {object} 天气工具条目。
 */
export function weatherTool(settings, transport) {
  let cached = null
  return syncTool(
    {
      name: 'get_weather',
      description:
        '查询指定城市的实时天气（温度、体感温度、湿度、风速及天气代码）；省略 city 时按全局配置或网络出口 IP 自动估计位置（可能与对方实际所在地不同）。',
      requiredCapabilities: ['network'],
      parametersSchema: parameters({
        city: {
          ...textParameter,
          description:
            '要查询的城市名称（如“上海”、“Tokyo”）；已知对方所在或询问的城市时请优先显式传入。'
        }
      })
    },
    async (_, input, source, signal) => {
      const options = (await settings.get()).tools.weather
      const city = input.city?.trim() || options.city.trim()
      let location
      let locationSource
      if (city) {
        const url = new URL('https://geocoding-api.open-meteo.com/v1/search')
        url.search = new URLSearchParams({ name: city, count: '1', language: 'zh' }).toString()
        const data = await fetchJson(transport, url, {}, signal)
        location = data.results?.[0]
        requireValue(location, 'weather_location_missing', '找不到该城市，请提供更明确的位置。')
        locationSource = input.city?.trim() ? 'argument' : 'configuration'
      } else {
        requireValue(options.ipLocation, 'weather_location_required', '请提供城市。')
        if (!cached || cached.expiresAt <= Date.now()) {
          const data = await fetchJson(transport, 'https://ipwho.is/', {}, signal)
          requireValue(
            data.success === true,
            'weather_location_required',
            '自动定位失败，请提供城市。'
          )
          cached = {
            expiresAt: Date.now() + 30 * 60000,
            location: {
              name: data.city,
              country: data.country,
              latitude: data.latitude,
              longitude: data.longitude
            }
          }
        }
        location = cached.location
        locationSource = 'ip'
      }
      requireValue(
        Number.isFinite(location.latitude) && Number.isFinite(location.longitude),
        'invalid_weather_location'
      )
      const url = new URL('https://api.open-meteo.com/v1/forecast')
      url.search = new URLSearchParams({
        latitude: String(location.latitude),
        longitude: String(location.longitude),
        current:
          'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m',
        timezone: 'auto'
      }).toString()
      const data = await fetchJson(transport, url, {}, signal)
      requireValue(data.current && data.current_units, 'invalid_weather_response')
      return {
        city: location.name,
        country: location.country,
        locationSource,
        timeZone: data.timezone,
        current: data.current,
        units: data.current_units
      }
    }
  )
}
