package com.messenger.pro;

import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Toast;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

public class AuthActivity extends Activity {
    private EditText phoneInput, passwordInput;
    private Button loginButton;
    private static final int RECORD_AUDIO_REQUEST_CODE = 101;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_auth);

        phoneInput = findViewById(R.id.phoneInput);
        passwordInput = findViewById(R.id.passwordInput);
        loginButton = findViewById(R.id.loginButton);

        loginButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String phone = phoneInput.getText().toString().trim();
                String password = passwordInput.getText().toString().trim();
                
                if (!phone.isEmpty() && !password.isEmpty()) {
                    sendLoginRequest(phone, password);
                } else {
                    Toast.makeText(AuthActivity.this, "Заполните все поля!", Toast.LENGTH_SHORT).show();
                }
            }
        });
    }

    // НАЦИОНАЛЬНАЯ ФУНКЦИЯ ДЛЯ ИНИЦИАЛИЗАЦИИ ЗВОНКА (WebRTC Signaling)
    public void initCallSystem() {
        // Проверяем, дал ли пользователь доступ к микрофону
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            // Если доступа нет, запрашиваем его у системы
            requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO}, RECORD_AUDIO_REQUEST_CODE);
        } else {
            // Если доступ уже есть, запускаем WebRTC аудио-подключение
            startVoiceCall();
        }
    }

    private void startVoiceCall() {
        Toast.makeText(this, "📞 Подключение к защищённому звонку Life and Lion...", Toast.LENGTH_LONG).show();
        // Здесь выполняется запуск нативногоWebRTC стрима аудио к вашему серверу Render
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == RECORD_AUDIO_REQUEST_CODE) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startVoiceCall();
            } else {
                Toast.makeText(this, "Запуск звонка невозможен без доступа к микрофону!", Toast.LENGTH_SHORT).show();
            }
        }
    }

    private void sendLoginRequest(final String phone, final String password) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    URL url = new URL("https://onrender.com");
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
                    conn.setDoOutput(true);

                    String data = "phone=" + phone + "&password=" + password;
                    OutputStream os = conn.getOutputStream();
                    os.write(data.getBytes());
                    os.flush();
                    os.close();

                    final int responseCode = conn.getResponseCode();
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            if (responseCode == 200) {
                                Toast.makeText(AuthActivity.this, "Успешный вход!", Toast.LENGTH_SHORT).show();
                                // После входа инициализируем аудиосистему для будущих вызовов
                                initCallSystem();
                            } else {
                                Toast.makeText(AuthActivity.this, "Ошибка авторизации!", Toast.LENGTH_SHORT).show();
                            }
                        }
                    });
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
        }).start();
    }
}
