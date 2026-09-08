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
  var chartGeom = {};
  var tipState = {};
  var HOVER_R = 12;

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
    tipState[canvasId] = -2;
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
      chartGeom[canvasId] = null;
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
    var geom = {
      canvasId: canvasId, points: points, color: color, unit: unit,
      clampMin: clampMin, clampMax: clampMax, dec: dec, xs: [], ys: []
    };
    for (i = 0; i < points.length; i++) {
      geom.xs.push(xPos(i));
      geom.ys.push(yPos(points[i].value));
      ctx.beginPath();
      ctx.arc(geom.xs[i], geom.ys[i], 3, 0, 2 * Math.PI);
      ctx.fill();
    }
    chartGeom[canvasId] = geom;
    attachTooltip(canvasId);
    ctx.textBaseline = 'alphabetic';
  }

  function redrawChart(canvasId) {
    var geom = chartGeom[canvasId];
    if (!geom) return;
    drawHourlyChart(geom.canvasId, geom.points, geom.color,
      geom.unit, geom.clampMin, geom.clampMax);
  }

  function roundRectPath(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawTooltip(canvasId, idx) {
    var geom = chartGeom[canvasId];
    if (!geom || idx < 0) {
      redrawChart(canvasId);
      return;
    }
    redrawChart(canvasId);
    tipState[canvasId] = idx;
    var canvas = document.getElementById(canvasId);
    var ctx = canvas.getContext('2d');
    var x = geom.xs[idx], y = geom.ys[idx];
    ctx.beginPath();
    ctx.arc(x, y, 6, 0, 2 * Math.PI);
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, 2 * Math.PI);
    ctx.fillStyle = geom.color;
    ctx.fill();
    var valText = geom.points[idx].value.toFixed(geom.dec) + geom.unit;
    var timeText = fmtHourLabel(geom.points[idx].time);
    ctx.font = 'bold 12px Arial';
    var wVal = ctx.measureText(valText).width;
    ctx.font = '11px Arial';
    var wTime = ctx.measureText(timeText).width;
    var bw = Math.max(wVal, wTime) + 16, bh = 40;
    var bx = x + 12, by = y - bh - 10;
    if (bx + bw > canvas.width - 4) bx = x - bw - 12;
    if (by < 4) by = y + 14;
    roundRectPath(ctx, bx, by, bw, bh, 5);
    ctx.fillStyle = 'rgba(255,255,255,0.95)';
    ctx.fill();
    ctx.strokeStyle = '#999';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = '#222222';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.font = 'bold 12px Arial';
    ctx.fillText(valText, bx + 8, by + 5);
    ctx.font = '11px Arial';
    ctx.fillStyle = '#666666';
    ctx.fillText(timeText, bx + 8, by + 22);
    ctx.textBaseline = 'alphabetic';
  }

  function hitPoint(canvasId, mx, my) {
    var geom = chartGeom[canvasId];
    if (!geom) return -1;
    var best = -1, bestD = HOVER_R * HOVER_R, i, dx, dy, d;
    for (i = 0; i < geom.xs.length; i++) {
      dx = geom.xs[i] - mx;
      dy = geom.ys[i] - my;
      d = dx * dx + dy * dy;
      if (d <= bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  }

  function eventPos(canvas, e) {
    var r = canvas.getBoundingClientRect();
    var t = (e.touches && e.touches.length) ? e.touches[0] : e;
    return { x: t.clientX - r.left, y: t.clientY - r.top };
  }

  function attachTooltip(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || canvas._tipAttached) return;
    canvas._tipAttached = true;
    var pending = null, scheduled = false;
    function onMove(e) {
      var p = eventPos(canvas, e);
      if (e.cancelable && e.type === 'touchmove') e.preventDefault();
      pending = p;
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(function () {
        scheduled = false;
        var idx = hitPoint(canvasId, pending.x, pending.y);
        if (idx === tipState[canvasId]) return;
        tipState[canvasId] = idx;
        canvas.style.cursor = idx >= 0 ? 'pointer' : 'default';
        drawTooltip(canvasId, idx);
      });
    }
    function onLeave() {
      tipState[canvasId] = -2;
      canvas.style.cursor = 'default';
      redrawChart(canvasId);
    }
    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('mouseleave', onLeave);
    canvas.addEventListener('touchstart', onMove, { passive: true });
    canvas.addEventListener('touchmove', onMove, { passive: false });
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

  function onDbError(e) {
    var msg = e && e.message ? e.message : e;
    console.error('[FB] Ошибка подписки:', msg);
    var badge = document.getElementById('dbError');
    if (badge) badge.innerText = 'Ошибка чтения базы: ' + msg;
  }

  renderHourly();

  db.ref('devices/' + DEV + '/current').on('value', function (snap) {
    var cur = snap.val();
    if (!cur) return;
    if (cur.temp !== null && cur.temp !== undefined) {
      document.getElementById('t').innerText = Number(cur.temp).toFixed(1);
      document.getElementById('h').innerText = Number(cur.hum).toFixed(1);
    }
    document.getElementById('updated').innerText = fmtUpdated(cur.ts);
  }, onDbError);

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
  }, onDbError);

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
