/* Browser location acquisition. Attendance submission remains in app.js. */
(function (root) {
  'use strict';
  function failure(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }
  function readableError(error) {
    if (error instanceof Error && typeof error.code === 'string') return error;
    if (error?.code === 1) return failure('PERMISSION_DENIED', 'Location permission is blocked. Allow Location for this site and for your browser in your phone settings, then retry.');
    if (error?.code === 3) return failure('TIMEOUT', 'Your phone could not find its location in time. Keep this page open, turn on Location and Wi-Fi or mobile data, and try again near a window or outdoors.');
    return failure('POSITION_UNAVAILABLE', 'Your phone could not provide a location. Turn on Location and an internet connection. If you opened the portal inside WhatsApp or another app, open it in Chrome or Safari and retry.');
  }
  function validate(position) {
    const c = position?.coords;
    const sample = {lat:c?.latitude, lng:c?.longitude, accuracy:c?.accuracy, timestamp:position?.timestamp};
    if (!Object.values(sample).every(x => typeof x === 'number' && Number.isFinite(x)) || sample.lat < -90 || sample.lat > 90 || sample.lng < -180 || sample.lng > 180 || sample.accuracy < 0) {
      throw failure('INVALID_LOCATION', 'Your phone returned an invalid location. Turn on Location and try again.');
    }
    if (Math.abs(Date.now() - sample.timestamp) > 120000) {
      throw failure('STALE_LOCATION', 'The location reading is out of date. Set your phone date and time to automatic and retry.');
    }
    if (sample.accuracy > 10000) {
      throw failure('LOW_ACCURACY', 'Your location is too approximate. Enable precise location and try again near a window or outdoors.');
    }
    return sample;
  }
  function requestPosition(options) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback(value);
      };
      // Native geolocation timeouts exclude time spent awaiting permission.
      const timer = setTimeout(() => finish(reject, failure('PERMISSION_WAIT', 'Location is still waiting for your phone. Respond to the browser permission prompt, keep this page open, then retry.')), 45000);
      try {
        root.navigator.geolocation.getCurrentPosition(
          position => {try {finish(resolve, validate(position));} catch (error) {finish(reject, error);}},
          error => finish(reject, readableError(error)),
          options
        );
      } catch (error) {finish(reject, readableError(error));}
    });
  }
  async function getLocation(onProgress = () => {}) {
    if (root.isSecureContext === false) throw failure('INSECURE_CONTEXT', 'Open the secure https:// portal address in Chrome or Safari to use location.');
    if (!root.navigator?.geolocation) throw failure('UNSUPPORTED', 'This browser cannot provide location. Open the portal directly in Chrome or Safari.');
    onProgress('Getting your location… Tap Allow if your browser asks.');
    try {
      return await requestPosition({enableHighAccuracy:true, timeout:12000, maximumAge:0});
    } catch (error) {
      if (!['TIMEOUT', 'POSITION_UNAVAILABLE', 'INVALID_LOCATION', 'STALE_LOCATION', 'LOW_ACCURACY'].includes(error.code)) throw error;
      onProgress('Still finding your location… Retrying with standard accuracy.');
      // Ask the browser for a fresh reading using its available location sources.
      return requestPosition({enableHighAccuracy:false, timeout:15000, maximumAge:0});
    }
  }
  root.CosmosLocation = Object.freeze({getLocation});
})(globalThis);
