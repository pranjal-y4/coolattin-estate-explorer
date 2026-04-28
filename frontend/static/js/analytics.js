{% extends "base.html" %}
{% block content %}

<section class="section" id="analytics">
  <div class="container center-head">
    <h2 class="section-title">Analytics Dashboard</h2>
    <p class="section-sub">Computed directly from your datasets in <code>/data</code>.</p>
  </div>

  <div class="container analytics-top">
    <div class="panel soft">
      <div class="panel-title">Datasets</div>
      <div class="dataset-picker">
        {% for did, dname in datasets %}
          <a class="dataset-pill {% if did == current_dataset_id %}active{% endif %}"
             href="{{ url_for('analytics_dataset', dataset_id=did) }}">
            {{ dname }}
          </a>
        {% endfor %}
      </div>
      {% if result %}
        <div class="note" style="margin-top:12px;">
          <strong>{{ result.dataset_name }}</strong> — {{ result.description }}
        </div>
      {% endif %}
    </div>

    <div class="panel soft">
      <div class="panel-title">Credibility</div>
      <div class="cred-list">
        <div class="cred-item"><span class="cred-dot"></span> Metrics are computed from raw files</div>
        <div class="cred-item"><span class="cred-dot"></span> Column detection is shown transparently</div>
        <div class="cred-item"><span class="cred-dot"></span> Errors are surfaced with tracebacks</div>
      </div>
      <div class="note">If a dataset fails, its traceback appears below so you can fix schema/path issues fast.</div>
    </div>
  </div>

  {% if result %}
    <div class="container kpi-grid">
      {% for k in result.kpis %}
        <div class="kpi-card">
          <div class="kpi-value">{{ k.value }}</div>
          <div class="kpi-label">{{ k.label }}</div>
          {% if k.hint %}
            <div class="kpi-hint">{{ k.hint }}</div>
          {% endif %}
        </div>
      {% endfor %}
    </div>

    <div class="container charts-grid">
      {% for c in result.charts %}
        <div class="panel soft chart-card">
          <div class="panel-title">{{ c.title }}</div>
          <canvas id="{{ c.chart_id }}" height="140"></canvas>
          <script type="application/json" id="{{ c.chart_id }}-config">
            { "type": "{{ c.type }}", "data": {{ c.data | tojson }}, "options": {{ (c.options or {}) | tojson }} }
          </script>
        </div>
      {% endfor %}
    </div>

    <div class="container" style="margin-top:16px;">
      <div class="panel soft">
        <div class="panel-title">Notes</div>
        <ul class="notes">
          {% for n in result.notes %}
            <li>{{ n }}</li>
          {% endfor %}
        </ul>
      </div>
    </div>
  {% endif %}

  {% if error %}
    <div class="container" style="margin-top:16px;">
      <div class="panel soft" style="border-color: rgba(220,38,38,.25);">
        <div class="panel-title">Dataset Error Traceback</div>
        <pre style="white-space:pre-wrap; font-size:12px; color: rgba(16,24,40,.85);">{{ error }}</pre>
      </div>
    </div>
  {% endif %}
</section>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script defer src="{{ url_for('static', filename='js/analytics.js') }}"></script>

{% endblock %}
