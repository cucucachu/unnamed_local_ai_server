Pod::Spec.new do |s|
  s.name           = 'HomeAIDeviceKey'
  s.version        = '1.0.0'
  s.summary        = 'Home AI host-app hardware-backed signing (iOS stub)'
  s.description    = 'M15-06 is Android first; iOS throws ios_not_implemented.'
  s.license        = 'UNLICENSED'
  s.author         = 'Home AI'
  s.homepage       = 'https://github.com/cucucachu/unnamed_local_ai_server'
  s.platforms      = { :ios => '15.1' }
  s.source         = { git: '' }
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.source_files = '*.swift'
end
