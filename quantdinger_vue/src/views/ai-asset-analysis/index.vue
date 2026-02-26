<template>
  <div class="ai-asset-analysis-page" :class="{ 'theme-dark': isDarkTheme }">

    <!-- ======== Trading Opportunities (Carousel) ======== -->
    <div class="opp-section" v-if="opportunities.length > 0">
      <div class="opp-header">
        <span class="opp-title"><a-icon type="radar-chart" /> {{ $t('aiAssetAnalysis.opportunities.title') }}</span>
        <span class="opp-header-right">
          <span class="opp-update-hint">
            <a-icon type="clock-circle" /> {{ $t('aiAssetAnalysis.opportunities.updateHint') }}
          </span>
          <a-button type="link" size="small" icon="reload" :loading="oppLoading" @click="loadOpportunities(true)">
            {{ $t('common.refresh') || '刷新' }}
          </a-button>
        </span>
      </div>
      <div
        class="opp-carousel-wrapper"
        @mouseenter="oppHover = true"
        @mouseleave="oppHover = false"
      >
        <div class="opp-track" :class="{ paused: oppHover }" :style="oppTrackStyle">
          <div
            class="opp-card"
            v-for="(opp, idx) in carouselItems"
            :key="'opp-' + idx"
            :class="[opp.impact, 'market-' + (opp.market || '').toLowerCase()]"
            @click="analyzeOpportunity(opp)"
          >
            <div class="opp-top">
              <span class="opp-symbol">{{ opp.symbol }}</span>
              <a-tag :color="getMarketTagColor(opp.market)" size="small" class="opp-market-tag">
                {{ getMarketLabel(opp.market) }}
              </a-tag>
            </div>
            <div class="opp-price">${{ formatOppPrice(opp.price) }}</div>
            <div class="opp-change" :class="opp.change_24h >= 0 ? 'up' : 'down'">
              {{ opp.change_24h >= 0 ? '+' : '' }}{{ (opp.change_24h || 0).toFixed(1) }}%
            </div>
            <div class="opp-signal">
              <a-tag :color="getSignalColor(opp.signal)" size="small">{{ getSignalLabel(opp.signal) }}</a-tag>
            </div>
            <div class="opp-reason">{{ getReasonText(opp) }}</div>
            <div class="opp-action">
              <a-icon type="thunderbolt" /> {{ $t('aiAssetAnalysis.opportunities.analyze') }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ======== Main Workspace Card with Tabs ======== -->
    <a-card :bordered="false" class="workspace-card">
      <a-tabs v-model="activeTab" class="workspace-tabs" size="large">
        <a-tab-pane key="quick">
          <span slot="tab">
            <a-icon type="thunderbolt" />
            {{ $t('aiAssetAnalysis.tabs.quick') }}
          </span>
          <div class="tab-body">
            <AnalysisView
              v-if="activeTab === 'quick'"
              :embedded="true"
              :preset-symbol="presetSymbol"
              :auto-analyze-signal="autoAnalyzeSignal"
            />
          </div>
        </a-tab-pane>
        <a-tab-pane key="monitor">
          <span slot="tab">
            <a-icon type="eye" />
            {{ $t('aiAssetAnalysis.tabs.monitor') }}
          </span>
          <div class="tab-body">
            <PortfolioView
              v-if="activeTab === 'monitor'"
              :embedded="true"
            />
          </div>
        </a-tab-pane>
      </a-tabs>
    </a-card>

  </div>
</template>

<script>
import { mapState } from 'vuex'
import AnalysisView from '@/views/ai-analysis'
import PortfolioView from '@/views/portfolio'
import { getTradingOpportunities } from '@/api/global-market'

export default {
  name: 'AIAssetAnalysis',
  components: {
    AnalysisView,
    PortfolioView
  },
  data () {
    return {
      activeTab: 'quick',
      // Opportunities (Carousel)
      opportunities: [],
      oppLoading: false,
      oppHover: false,
      // Props passed to AnalysisView
      presetSymbol: '',
      autoAnalyzeSignal: 0
    }
  },
  computed: {
    ...mapState({
      navTheme: state => state.app.theme
    }),
    isDarkTheme () {
      return this.navTheme === 'dark' || this.navTheme === 'realdark'
    },
    // Duplicate items for seamless infinite carousel
    carouselItems () {
      if (this.opportunities.length === 0) return []
      // Duplicate the array so the CSS animation can loop seamlessly
      return [...this.opportunities, ...this.opportunities]
    },
    oppTrackStyle () {
      // Animation duration proportional to number of items (3s per card)
      const duration = this.opportunities.length * 3
      return {
        animationDuration: duration + 's'
      }
    }
  },
  created () {
    this.loadOpportunities()
  },
  methods: {
    // ==================== Trading Opportunities (Carousel) ====================
    async loadOpportunities (force = false) {
      this.oppLoading = true
      try {
        const params = force ? { force: true } : {}
        const res = await getTradingOpportunities(params)
        if (res && res.code === 1 && Array.isArray(res.data)) {
          this.opportunities = res.data.slice(0, 20)
        }
      } catch (e) {
        console.warn('Load opportunities failed:', e)
      } finally {
        this.oppLoading = false
      }
    },
    getSignalColor (signal) {
      const map = {
        overbought: 'volcano',
        oversold: 'green',
        bullish_momentum: 'cyan',
        bearish_momentum: 'red'
      }
      return map[signal] || 'default'
    },
    getSignalLabel (signal) {
      const key = `aiAssetAnalysis.opportunities.signal.${signal}`
      const translated = this.$t(key)
      // If i18n returns the key itself, fall back to the raw signal name
      return translated !== key ? translated : signal
    },
    getMarketTagColor (market) {
      const colors = {
        Crypto: 'purple',
        USStock: 'green',
        Forex: 'gold'
      }
      return colors[market] || 'default'
    },
    getMarketLabel (market) {
      const key = `aiAssetAnalysis.opportunities.market.${market}`
      const translated = this.$t(key)
      return translated !== key ? translated : market
    },
    getReasonText (opp) {
      const market = (opp.market || 'Crypto').toLowerCase()
      const signal = opp.signal || ''
      const key = `aiAssetAnalysis.opportunities.reason.${market}.${signal}`
      const translated = this.$t(key)
      if (translated === key) {
        // i18n key not found, fall back to backend reason
        return opp.reason || ''
      }
      const change = Math.abs(opp.change_24h || 0).toFixed(1)
      const change7d = Math.abs(opp.change_7d || 0).toFixed(1)
      return translated.replace('{change}', change).replace('{change7d}', change7d)
    },
    formatOppPrice (price) {
      if (!price) return '--'
      if (price >= 10000) return (price / 1000).toFixed(1) + 'K'
      if (price >= 1) return price.toFixed(2)
      return price.toFixed(4)
    },
    analyzeOpportunity (opp) {
      this.activeTab = 'quick'
      const market = opp.market || 'Crypto'
      this.presetSymbol = `${market}:${opp.symbol}`
      this.$nextTick(() => {
        this.autoAnalyzeSignal++
      })
    }
  }
}
</script>

<style lang="less" scoped>
.ai-asset-analysis-page {
  padding: 16px;
  min-height: calc(100vh - 120px);
  background: #f0f2f5;

  /* ==================== Trading Opportunities (Carousel) ==================== */
  .opp-section {
    margin-bottom: 12px;

    .opp-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
      padding: 0 4px;

      .opp-title {
        font-size: 14px;
        font-weight: 600;
        color: #1a1a2e;
      }

      .opp-header-right {
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .opp-update-hint {
        font-size: 12px;
        color: #8c8c8c;
      }
    }

    .opp-carousel-wrapper {
      overflow: hidden;
      position: relative;
      border-radius: 10px;

      // Fade masks on left and right edges
      &::before,
      &::after {
        content: '';
        position: absolute;
        top: 0;
        bottom: 0;
        width: 40px;
        z-index: 2;
        pointer-events: none;
      }

      &::before {
        left: 0;
        background: linear-gradient(to right, #f0f2f5, transparent);
      }

      &::after {
        right: 0;
        background: linear-gradient(to left, #f0f2f5, transparent);
      }
    }

    .opp-track {
      display: flex;
      gap: 10px;
      animation: opp-scroll-left 60s linear infinite;
      width: max-content;
      padding: 4px 0;

      &.paused {
        animation-play-state: paused;
      }
    }

    @keyframes opp-scroll-left {
      0% {
        transform: translateX(0);
      }
      100% {
        transform: translateX(-50%);
      }
    }

    .opp-card {
      width: 190px;
      background: #fff;
      border-radius: 10px;
      padding: 12px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
      cursor: pointer;
      transition: all 0.2s;
      border-left: 3px solid #d9d9d9;
      flex-shrink: 0;

      &.bullish {
        border-left-color: #52c41a;
      }

      &.bearish {
        border-left-color: #ff4d4f;
      }

      &:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
      }

      .opp-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 4px;
      }

      .opp-symbol {
        font-weight: 700;
        font-size: 14px;
        color: #1a1a2e;
      }

      .opp-market-tag {
        font-size: 11px;
      }

      .opp-price {
        font-size: 13px;
        color: #595959;
      }

      .opp-change {
        font-size: 15px;
        font-weight: 600;

        &.up {
          color: #52c41a;
        }

        &.down {
          color: #ff4d4f;
        }
      }

      .opp-signal {
        margin-top: 4px;
      }

      .opp-reason {
        font-size: 11px;
        color: #8c8c8c;
        margin-top: 4px;
        line-height: 1.4;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }

      .opp-action {
        margin-top: 6px;
        font-size: 12px;
        color: #1890ff;
        font-weight: 500;
      }
    }
  }

  /* ==================== Workspace Card ==================== */
  .workspace-card {
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);

    ::v-deep .ant-card-body {
      padding: 0;
    }

    .workspace-tabs {
      ::v-deep .ant-tabs-bar {
        margin-bottom: 0;
        padding: 0 16px;
        border-bottom: 1px solid #f0f0f0;
      }

      ::v-deep .ant-tabs-tab {
        font-size: 15px;
        padding: 14px 16px;
      }
    }

    .tab-body {
      ::v-deep .ai-analysis-container.embedded {
        border-radius: 0;
        overflow: hidden;
      }

      ::v-deep .portfolio-container.embedded {
        border-radius: 0;
        overflow: hidden;
      }
    }
  }

  /* ==================== Dark Theme ==================== */
  &.theme-dark {
    background: #0d1117;

    .opp-section {
      .opp-header .opp-title {
        color: #e6edf3;
      }

      .opp-header .opp-update-hint {
        color: #8b949e;
      }

      .opp-carousel-wrapper {
        &::before {
          background: linear-gradient(to right, #0d1117, transparent);
        }

        &::after {
          background: linear-gradient(to left, #0d1117, transparent);
        }
      }

      .opp-card {
        background: #161b22;
        border-color: #30363d;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);

        .opp-symbol {
          color: #e6edf3;
        }

        .opp-price {
          color: #8b949e;
        }

        .opp-reason {
          color: #8b949e;
        }

        .opp-action {
          color: #58a6ff;
        }

        &:hover {
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
        }
      }
    }

    .workspace-card {
      background: #161b22;
      border-color: #30363d;

      .workspace-tabs {
        ::v-deep .ant-tabs-bar {
          border-bottom-color: #30363d;
        }

        ::v-deep .ant-tabs-tab {
          color: #8b949e;

          &:hover {
            color: #c9d1d9;
          }
        }

        ::v-deep .ant-tabs-tab-active {
          color: #58a6ff;
        }

        ::v-deep .ant-tabs-ink-bar {
          background-color: #58a6ff;
        }
      }
    }

  }
}
</style>
