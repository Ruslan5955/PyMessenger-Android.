package com.messenger.pro;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        WebView webView = new WebView(this);
        setContentView(webView);

        WebSettings webSettings = webView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);

        webView.setWebViewClient(new WebViewClient());

        // ВАЖНО: Замени эту ссылку на адрес своего запущенного Flask-сервера!
        // Если сервера в интернете пока нет, можно временно оставить эту
       webView.loadUrl("https://pymessenger-android.onrender.com/");
    }
}
