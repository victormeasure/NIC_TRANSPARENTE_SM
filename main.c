
#include "header.h"
#include "sl_main_init.h"
#include "sl_main_process_action.h"



int main(void)
{

  sl_main_init();

  app_init();



  while (1) {

    sl_main_process_action();


    app_process_action();

  }

}
