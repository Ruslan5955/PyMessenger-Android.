package com.messenger.pro;

import android.app.Activity;
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
