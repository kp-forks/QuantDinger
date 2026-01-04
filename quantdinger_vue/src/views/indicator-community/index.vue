<template>
  <div class="indicator-community-container">
    <div class="community-banner">
      <a-icon type="info-circle" />
      <span class="banner-text">{{ $t('community.banner.loginHint') }}</span>
      <a-button type="link" size="small" @click="jumpToLogin" class="jump-button">
        {{ $t('community.banner.jumpButton') }}
      </a-button>
    </div>
    <div class="indicator-community-iframe">
      <iframe
        class="community-iframe"
        :src="url"
        title="Indicator Community"
        frameborder="0"
        referrerpolicy="no-referrer-when-downgrade"
        allow="clipboard-read; clipboard-write; fullscreen"
      />
    </div>
  </div>
</template>

<script>
export default {
  name: 'IndicatorCommunity',
  data () {
    return {
      url: 'https://www.quantdinger.com/'
    }
  },
  methods: {
    jumpToLogin () {
      // 跳转到独立页面，避免CSRF问题
      window.open('https://www.quantdinger.com/', '_blank')
    }
  }
}
</script>

<style lang="less" scoped>
.indicator-community-container {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
}

.community-banner {
  display: flex;
  align-items: center;
  padding: 12px 12px;
  background: #e6f7ff;
  border: 1px solid #91d5ff;
  border-radius: 0;
  margin: 0;
  flex-shrink: 0;
  font-size: 14px;
  color: #1890ff;

  .anticon {
    margin-right: 8px;
    font-size: 16px;
  }

  .banner-text {
    flex: 1;
  }

  .jump-button {
    padding: 0;
    height: auto;
    margin-left: 8px;
  }
}

.indicator-community-iframe {
  position: relative;
  // left: -24px;
  width: calc(100%);
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.community-iframe {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
  background: transparent;
}
</style>
