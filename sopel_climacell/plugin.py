# coding=utf8
"""sopel-climacell

A weather plugin for Sopel using ClimaCell API. Heavily influenced by RustyB's
sopel-weather plugin https://github.com/RustyBower/sopel-weather
"""
from __future__ import unicode_literals, absolute_import, division, print_function

from string import Template

from sopel.config.types import (
    ListAttribute,
    StaticSection,
    ValidatedAttribute,
    ChoiceAttribute,
    NO_DEFAULT,
)
from sopel.formatting import color, colors, bold
from sopel.module import commands, example, NOLIMIT
from sopel import tools
from sopel.modules.units import c_to_f, f_to_c
from sopel.tools.time import (
    format_time,
    get_channel_timezone,
    get_nick_timezone,
    get_timezone,
    validate_timezone
)

import pendulum
import requests


LOGGER = tools.get_logger(__name__)


WEATHER_CODE_DESCRIPTIONS = {
    "rain_heavy":          "🌧️ Substantial rain",
    "rain":                "🌧️ Rain",
    "rain_light":          "🌧️ Light rain",
    "freezing_rain_heavy": "🧊🌧️ Substantial freezing rain",
    "freezing_rain":       "🧊🌧️ Freezing rain",
    "freezing_rain_light": "🧊🌧️ Light freezing rain",
    "freezing_drizzle":    "🧊🌧️ Light freezing rain falling in fine pieces",
    "drizzle":             "🌦️ Drizzle",
    "ice_pellets_heavy":   "🧊 Substantial ice pellets",
    "ice_pellets":         "🧊 Ice pellets",
    "ice_pellets_light":   "🧊 Light ice pellets",
    "snow_heavy":          "❄️ Substantial snow",
    "snow":                "❄️ Snow",
    "snow_light":          "❄️ Light snow",
    "flurries":            "❄️ Flurries",
    "tstorm":              "🌩️ Thunderstorm conditions",
    "fog_light":           "🌁 Light fog",
    "fog":                 "🌁 Fog",
    "cloudy":              "☁️ Cloudy",
    "mostly_cloudy":       "🌥️ Mostly cloudy",
    "partly_cloudy":       "⛅ Partly cloudy",
    "mostly_clear":        "🌤️ Mostly clear",
    "clear":               "☀️ Clear",
}

MAPPED_FIELDS = {
    "weather_code": bold("Conditions:"),
    "feels_like": bold("Feels Like:"),
    "dewpoint": bold("Dewpoint:"),
    "humidity": bold("Humidity:"),
    "wind_speed": bold("Wind Speed:"),
    "wind_direction": bold("Wind Direction:"),
    "wind_gust": bold("Wind Gust:"),
    "baro_pressure": bold("Pressure:"),
    "precipitation": bold("Precipitation:"),
    "precipitation_type": bold("Precipitation:"),
    "sunrise": bold("Sunrise:"),
    "sunset": bold("Sunset:"),
    "visibility": bold("Visibility:"),
    "cloud_cover": bold("Cloud Cover:"),
    "cloud_base": bold("Cloud Base:"),
    "cloud_ceiling": bold("Cloud Ceiling:"),
    "surface_shortwave_radiation": bold("Solar Radiation:"),
    "moon_phase": bold("Moon P​hase:"),
    "epa_health_concern": bold("Air Quality:"),
}

SORTED_FIELDS = {
    "weather_code": 3,
    "temp": 1,
    "feels_like": 2,
    "dewpoint": 7,
    "humidity": 6,
    "wind_speed": 8,
    "wind_direction": 10,
    "wind_gust": 9,
    "baro_pressure": 11,
    "precipitation": 5,
    "precipitation_type": 4,
    "sunrise": 12,
    "sunset": 13,
    "visibility": 14,
    "cloud_cover": 15,
    "cloud_base": 16,
    "cloud_ceiling": 17,
    "surface_shortwave_radiation": 18,
    "moon_phase": 19,
    "epa_health_concern": 20,
}


class ClimacellSection(StaticSection):
    google_api_key = ValidatedAttribute('google_api_key', default=NO_DEFAULT)
    """The Google API key to auth to the Google Geocoding endpoint"""

    climacell_api_key = ValidatedAttribute('climacell_api_key', default=NO_DEFAULT)
    """The ClimaCell API key to auth to the ClimaCell API endpoint"""

    now_info_items = ListAttribute(
        "now_info_items",
        default=["temp", "feels_like", "weather_code"],
    )
    """
    The items to include in the weather info message, after current conditions.
    Available: temp, feels_like, dewpoint, humidity, wind_speed, wind_direction, 
        wind_gust, baro_pressure, precipitation, precipitation_type, sunrise, 
        sunset, visibility, cloud_cover, cloud_base, cloud_ceiling, 
        surface_shortwave_radiation, moon_phase, epa_health_concern, weather_code
    """

    units = ChoiceAttribute(
        "units",
        ['us', 'si'],
        default='us'
    )
    """Which units to use when returning information (US or SI)"""


def configure(config):
    config.define_section('climacell', ClimacellSection, validate=False)
    config.climacell.configure_setting(
        "google_api_key", "Enter your Google API key.",
    )
    config.climacell.configure_setting(
        "climacell_api_key", "Enter your ClimaCell API key.",
    )
    config.climacell.configure_setting(
        "now_info_items", "Which attributes to show in response to weather"
    )
    config.climacell.configure_setting(
        "units", "Which units (US/SI) to show in response to weather"
    )


def setup(bot):
    bot.config.define_section('climacell', ClimacellSection)


@commands('weather', 'wz')
@example('.weather boston')
def weather(bot, trigger):
    if not bot.config.climacell.climacell_api_key or bot.config.climacell.climacell_api_key == '':
        return bot.reply("No ClimaCell API key found, please configure this plugin.")
    if not bot.config.climacell.google_api_key or bot.config.climacell.google_api_key == '':
        return bot.reply("No Google API key found, please configure this plugin.")

    location = trigger.group(2)
    if not location:
        latitude = bot.db.get_nick_value(trigger.nick, 'latitude')
        longitude = bot.db.get_nick_value(trigger.nick, 'longitude')
        location = bot.db.get_nick_value(trigger.nick, 'location')
        if not location:
            return bot.say(("I don't know where you live. "
                    "Give me a location, like {pfx}{command} London, "
                    "or tell me where you live by saying {pfx}setlocation "
                    "London, for example.").format(command=trigger.group(1),
                                                   pfx=bot.config.core.help_prefix))
    else:
        user_input = trigger.group(2).strip().lower()
        if user_input == "chaz":
            user_input = "capitol hill seattle"
        latitude, longitude, location = get_latlon(
            user_location = user_input,
            api_key = bot.config.climacell.google_api_key,
        )
        if not latitude:
            bot.reply("I couldn't find a location by that name.")
            return NOLIMIT

    api_key = bot.config.climacell.climacell_api_key

    channel_or_nick = tools.Identifier(trigger.nick)
    zone = _get_timezone(
        latitude, longitude, 
        pendulum.now().int_timestamp,
        bot.config.climacell.google_api_key
    )

    bundle = {
        'location': location,
        'latitude': latitude,
        'longitude': longitude,
        'api_key': api_key,
        'fields': ",".join(bot.config.climacell.now_info_items),
        'units': bot.config.climacell.units,
        'tz': zone
    }

    reply = get_weather(bundle)
    if len(repr(reply)) > 475:
        reply = reply.split(' | ')
        div = int(len(reply) / 2)
        bot.say(' | '.join(reply[:div]))
        bot.say(' | '.join(reply[div:]))
        return
    else:
        return bot.say(reply)


@commands('forecast', 'fc', 'wfc', 'wfz')
@example('.forecast boston')
def forecast(bot, trigger):
    """Fetches forecast for provided location"""
    pass


@commands('setlocation', 'setl', 'setw')
@example('.setlocation boston')
def set_location(bot, trigger):
    """Sets or updates a location for a user"""
    if not bot.config.climacell.google_api_key or bot.config.climacell.google_api_key == '':
        return bot.reply("No Google API key found, please configure this plugin.")

    # Return an error if no location is provided
    if not trigger.group(2):
        bot.reply('Give me a location, like "Boston" or "90210".')
        return NOLIMIT

    user_input = trigger.group(2).strip().lower()

    # Get GeoCoords
    latitude, longitude, location = get_latlon(
        user_location = user_input,
        api_key = bot.config.climacell.google_api_key,
    )

    if not latitude:
        bot.reply("I couldn't find a location by that name.")
        return NOLIMIT

    # Assign Latitude & Longitude to user
    bot.db.set_nick_value(trigger.nick, 'latitude', latitude)
    bot.db.set_nick_value(trigger.nick, 'longitude', longitude)
    bot.db.set_nick_value(trigger.nick, 'location', location)

    return bot.reply('I now have you at {}.'.format(location))


def get_latlon(user_location, api_key):
    """Gets latitude and longitude for a location"""
    url = "https://maps.googleapis.com/maps/api/geocode/json?address={user_location}&key={api_key}"
    lat = lon = loc = None
    
    try:
        data = requests.get(url.format(
            user_location = user_location,
            api_key = api_key
        ))
        LOGGER.debug(data.url)
        data = data.json()

        data = data['results'][0]

        loc = data['formatted_address']
        lat = data['geometry']['location']['lat']
        lon = data['geometry']['location']['lng']
    except:
        pass

    return lat, lon, loc


def get_weather(bundle):
    """Gets weather for a user or location"""
    url = ("https://api.climacell.co/v3/weather/realtime?lat={lat}&lon={lon}"
           "&fields={fields}&unit_system={units}&apikey={api_key}").format(
               lat = bundle['latitude'],
               lon = bundle['longitude'],
               fields = bundle['fields'],
               units = bundle['units'],
               api_key = bundle['api_key']
           )

    data = requests.get(url)
    LOGGER.debug(data.url)
    data = data.json()

    sort_keys = {}
    for k in data:
        if SORTED_FIELDS.get(k):
            sort_keys[k] = SORTED_FIELDS.get(k)
        else:
            sort_keys[k] = 999
    sorted_dict = {k: data[k] for k in sorted(data, key=lambda x: sort_keys[x])}
    parsed_fields = _parse_data(sorted_dict, bundle['tz'])

    location = bold("[{}]".format(bundle['location']))
    base_string = "{} {} | Powered by ClimaCell API (https://www.climacell.co/weather-api)".format(
        location,
        parsed_fields
    )

    return base_string


def _parse_data(data, timezone):
    """parse data"""
    
    string = ""
    idx = 0
    for key,values in data.items():
        value_map = values
        if idx == 0:
            prefix = ""
        else:
            prefix = " | "

        if key in ["lat", "lon", "observation_time"]:
            continue
        if not value_map.get('value') or value_map.get('value') == 'none':
            continue

        if key == "weather_code":
            value_map = {'value': WEATHER_CODE_DESCRIPTIONS[values['value']]}

        if value_map.get('units'):
            if value_map['units'] == "F":
                f_color = _get_temp_color(value_map['value'])
                value_map = {
                    'value': "{}°F/{}°C".format(
                        color("{}".format(round(value_map['value'])), f_color),
                        color("{}".format(round(f_to_c(value_map['value']), 1)), f_color),
                    )
                }
            elif value_map['units'] == "C":
                f_color = _get_temp_color(c_to_f(value_map['value']))
                value_map = {
                    'value': "{}°C/{}°F".format(
                        color("{}".format(round(value_map['value'], 1)), f_color),
                        color("{}".format(round(c_to_f(value_map['value']))), f_color),
                    )
                }

        if key == "wind_direction":
            value_map = {'value': get_wind(values['value'])}

        if key in ["sunrise", "sunset"]:
            value_map = {'value': _parse_time(values['value'], timezone)}
        
        try:
            value_map['value'] = _get_value_format(value_map['value'], key)
        except:
            pass

        template = Template("{pfx}{field} $value$units").safe_substitute(value_map)
        string += template.format(
            pfx=prefix,
            field=MAPPED_FIELDS.get(key, ""),
        )
        idx += 1

    return string.replace('$units', '')


def _get_value_format(value, field):
    """get value format by field"""
    
    def moonphases():
        moon_phases = {
            "new": "🌑 new moon", 
            "new_moon": "🌑 new moon", 
            "waxing_crescent": "🌒 waxing crescent (1/4 full)", 
            "first_quarter": "🌓 half moon (first quarter)", 
            "waxing_gibbous": "🌔 waxing gibbous (3/4 full)", 
            "full": "🌕 full moon", 
            "waning_gibbous": "🌖 waning gibbous (3/4 full)", 
            "third_quarter": "🌗 half moon (last quarter)", 
            "last_quarter": "🌗 half moon (last quarter)", 
            "waning_crescent": "🌘 waning crescent (1/4 full)"
        }
        return moon_phases[value]

    def round_int():
        return "{}".format(round(value))

    def round_decimal():
        return "{:.2f}".format(round(value, 2))

    def stringify():
        return "{}".format(value)

    formats = {
        "humidity": round_int,
        "baro_pressure": round_decimal,
        "wind_speed": round_int,
        "wind_gust": round_int,
        "moon_phase": moonphases,
        "visibility": round_int,
        "surface_shortwave_radiation": round_int
    }

    func = formats.get(field) or stringify
    return func()


def get_wind(bearing):
    """get wind direction"""

    if (bearing <= 22.5) or (bearing > 337.5):
        bearing = u'\u2193 N'
    elif (bearing > 22.5) and (bearing <= 67.5):
        bearing = u'\u2199 NE'
    elif (bearing > 67.5) and (bearing <= 112.5):
        bearing = u'\u2190 E'
    elif (bearing > 112.5) and (bearing <= 157.5):
        bearing = u'\u2196 SE'
    elif (bearing > 157.5) and (bearing <= 202.5):
        bearing = u'\u2191 S'
    elif (bearing > 202.5) and (bearing <= 247.5):
        bearing = u'\u2197 SW'
    elif (bearing > 247.5) and (bearing <= 292.5):
        bearing = u'\u2192 W'
    elif (bearing > 292.5) and (bearing <= 337.5):
        bearing = u'\u2198 NW'

    return bearing


def _get_temp_color(f):
    """get color"""
    if f < 10:
        color = colors.LIGHT_BLUE
    elif f < 32:
        color = colors.TEAL
    elif f < 50:
        color = colors.BLUE
    elif f < 60:
        color = colors.LIGHT_GREEN
    elif f < 70:
        color = colors.GREEN
    elif f < 80:
        color = colors.YELLOW
    elif f < 90:
        color = colors.ORANGE
    else:
        color = colors.RED
    return color


def _parse_time(time, tz):
    """parse time"""
    return pendulum.parse(time, strict=False).in_tz(tz).format("h:mm A zz")


def _get_timezone(lat, lng, now, key):
    """get timezone"""
    url = ("https://maps.googleapis.com/maps/api/timezone/json"
           "?location={lat},{lng}"
           "&timestamp={now}"
           "&key={key}").format(lat=lat,lng=lng,now=now,key=key)
    data = requests.get(url).json()

    return data['timeZoneId']