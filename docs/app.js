// Живая страница показаний: Firebase onValue + canvas-графики с осями.
(function () {
  'use strict';

  firebase.initializeApp(window.FB_CONFIG);
  var DEV = window.FB_CONFIG.deviceId;
  var db = firebase.database();
  var TZ_KEY = 'tz_offset';
  var lastHourly = [];

  function loadTz() {
    try {
      var saved = parseInt(localStorage.getItem(TZ_KEY), 10);
      if (!isNaN(saved) && saved >= -12 && saved <= 14) return saved;
    } catch (e) { /* localStorage недоступен — дефолт ниже */ }
    return Math.round(-new Date().getTimezoneOffset() / 60);
  }

  var tz = loadTz();

  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  function fmtHourLabel(ts) {
    if (!ts) return '';
    var d = new Date((ts + tz * 3600) * 1000);
    return pad2(d.getUTCHours()) + ':00';
  }

  function fmtUpdated(ts) {
    if (!ts) return '--';
    var d = new Date((ts + tz * 3600) * 1000);
    return pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes()) +
      ':' + pad2(d.getUTCSeconds());
  }

  function fitCanvas(canvas) {
    var w = canvas.clientWidth || 300;
    var h = canvas.clientHeight || 200;
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
  }

  function decimalsForSpan(span) {
    if (span >= 10) return 0;
    if (span < 0.1) return 3;
    if (span < 1) return 2;
    return 1;
  }

  function drawHourlyChart(canvasId, points, color, unit,
      clampMin, clampMax) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    fitCanvas(canvas);
    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    var mL = 46, mB = 22, mT = 8, mR = 8;
    ctx.clearRect(0, 0, W, H);
    ctx.font = '11px Arial';
    if (points.length < 2) {
      ctx.fillStyle = '#999';
      ctx.font = '20px Arial';
      ctx.textAlign = 'center';
      ctx.fillText('Нет данных', W / 2, H / 2);
      return;
    }
    var values = points.map(function (p) { return p.value; });
    var yMin = Math.min.apply(null, values);
    var yMax = Math.max.apply(null, values);
    var span = (yMax - yMin) || 1;
    yMin -= span * 0.2;
    yMax += span * 0.2;
    if (yMax === yMin) yMax = yMin + 1;
    if (clampMin !== null) yMin = Math.max(clampMin, yMin);
    if (clampMax !== null) yMax = Math.min(clampMax, yMax);
    if (yMax <= yMin) yMax = yMin + 1;
    var dec = decimalsForSpan(yMax - yMin);
    var pw = W - mL - mR, ph = H - mT - mB;
    function xPos(i) { return mL + (i / (points.length - 1)) * pw; }
    function yPos(v) { return mT + ((yMax - v) / (yMax - yMin)) * ph; }
    var g, v, y, i;
    ctx.strokeStyle = '#e0e0e0';
    ctx.lineWidth = 1;
    ctx.fillStyle = '#666';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (g = 0; g <= 4; g++) {
      v = yMin + ((yMax - yMin) * g) / 4;
      y = yPos(v);
      ctx.beginPath();
      ctx.moveTo(mL, y);
      ctx.lineTo(W - mR, y);
      ctx.stroke();
      ctx.fillText(v.toFixed(dec) + unit, mL - 4, y);
    }
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    var step = Math.max(1, Math.ceil(points.length / 6));
    for (i = 0; i < points.length; i += step) {
      ctx.fillText(fmtHourLabel(points[i].time), xPos(i), H - mB + 4);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (i = 0; i < points.length; i++) {
      y = yPos(points[i].value);
      if (i === 0) ctx.moveTo(xPos(i), y);
      else ctx.lineTo(xPos(i), y);
    }
    ctx.stroke();
    ctx.fillStyle = color;
    for (i = 0; i < points.length; i++) {
      ctx.beginPath();
      ctx.arc(xPos(i), yPos(points[i].value), 3, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.textBaseline = 'alphabetic';
  }

  function renderHourly() {
    var temps = lastHourly.map(function (p) {
      return { time: p.time, value: p.temp };
    });
    var hums = lastHourly.map(function (p) {
      return { time: p.time, value: p.hum };
    });
    drawHourlyChart('c1', temps, '#e74c3c', '°', null, null);
    drawHourlyChart('c2', hums, '#3498db', '%', 0, 100);
  }

  db.ref('devices/' + DEV + '/current').on('value', function (snap) {
    var cur = snap.val();
    if (!cur) return;
    if (cur.temp !== null && cur.temp !== undefined) {
      document.getElementById('t').innerText = Number(cur.temp).toFixed(1);
      document.getElementById('h').innerText = Number(cur.hum).toFixed(1);
    }
    document.getElementById('updated').innerText = fmtUpdated(cur.ts);
  });

  db.ref('devices/' + DEV + '/hourly').on('value', function (snap) {
    var obj = snap.val() || {};
    var arr = Object.keys(obj).map(function (k) {
      return {
        time: Number(k),
        temp: Number(obj[k].temp),
        hum: Number(obj[k].hum)
      };
    });
    arr.sort(function (a, b) { return a.time - b.time; });
    lastHourly = arr.slice(-24);
    renderHourly();
  });

  document.getElementById('tz').value = tz;

  document.getElementById('tzSave').addEventListener('click', function () {
    var msg = document.getElementById('tzMsg');
    var val = parseInt(document.getElementById('tz').value, 10);
    if (isNaN(val) || val < -12 || val > 14) {
      msg.innerText = 'Нужно целое от -12 до +14';
      return;
    }
    tz = val;
    try {
      localStorage.setItem(TZ_KEY, String(val));
    } catch (e) { /* приватный режим — поправка до перезагрузки */ }
    renderHourly();
    msg.innerText = 'Сохранено в этом браузере';
  });
})();
